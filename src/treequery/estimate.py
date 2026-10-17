"""
estimate.py — how long will this search take?  Answered by timing a small, size-weighted sample of real reads.

Why sample, and why weight by size:

* The *sizes* of all files are already known exactly (stage 2 stats every file), so no size distribution has to be
  guessed.  What is unknown is how long a read takes: a fixed cost per file (open, parse headers) plus a cost per byte,
  both depending on the format (a 40 MB PDF is not 40 MB of ``.txt``).
* So the estimator (1) finds the files whose text would really be read — the same decision ``run_search`` makes, via
  ``SearchPlan`` — (2) draws a sample of them with probability proportional to size, so the few huge files that
  dominate the total are always represented, (3) times the real reads, (4) fits ``seconds = fixed + per_byte x size``
  for each file type, and (5) applies that to every file.  A bootstrap over the timed files gives an 80 % interval.
* A file type with only a few files is simply read in full; its time is measured, not modelled.

Example:
    est = estimate(ProfileSettings(roots=("D:/docs",), query="invoice", terms={...}), EstimateSettings(sample=40))
    print(format_estimate(est))
"""
import math
import os
import random
import time
from collections import defaultdict
from dataclasses import dataclass, field, replace
from typing import Callable

from loguru import logger

from .plan import Reader, SearchPlan, Stage
from .settings import option
from .units import format_duration, format_size
from .walk import FileEntry, WalkStats

BOOTSTRAP_ROUNDS = 300


@dataclass(frozen=True)
class EstimateSettings:
    folder: str = option("", "estimate only this folder (default: all roots)")
    recursive: bool = option(True, "include subfolders of --folder; --no-recursive estimates its own files only")
    sample: int = option(30, "how many files to time (more is slower but tighter)")
    min_per_type: int = option(3, "time at least this many files of each file type (or all of them, if fewer)")
    max_seconds: float = option(60.0, "stop timing after this many seconds, whatever --sample says")
    group_depth: int = option(1, "one report line per folder this many levels below each root; 0 = one total")
    interval: float = option(0.8, "width of the reported range, e.g. 0.8 = the 10th to 90th percentile")
    seed: int | None = option(None, "random seed, for repeatable samples")

    def __post_init__(self):
        if self.sample < 1 or self.min_per_type < 1:
            raise ValueError("sample and min_per_type must be at least 1")
        if not 0 < self.interval < 1:
            raise ValueError(f"interval must be between 0 and 1 (got {self.interval})")
        if self.group_depth < 0 or self.max_seconds <= 0:
            raise ValueError("group_depth must be >= 0 and max_seconds > 0")


# --------------------------------------------------------------------------- #
# Pure statistics (no files, no clock): easy to test
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Line:
    """seconds = fixed + per_byte x size, with the spread of the residuals."""
    fixed: float = 0.0
    per_byte: float = 0.0
    sd: float = 0.0

    def predict(self, size: float) -> float:
        return self.fixed + self.per_byte * size


def fit_line(points: list[tuple[float, float]]) -> Line:
    """
    Least-squares fit of seconds on bytes, kept physical: neither the fixed cost nor the rate may be negative.

    Example:
        fit_line([(0, 0.1), (1_000_000, 0.6), (2_000_000, 1.1)])   # -> fixed 0.1, 5e-7 s per byte
    """
    n = len(points)
    if n == 0:
        return Line()
    sizes, times = [p[0] for p in points], [p[1] for p in points]
    mean_s, mean_t = sum(sizes) / n, sum(times) / n
    spread = sum((s - mean_s) ** 2 for s in sizes)
    if n == 1 or spread == 0:
        return Line(fixed=mean_t)                                   # sizes tell us nothing: all cost is "per file"
    slope = sum((s - mean_s) * (t - mean_t) for s, t in points) / spread
    fixed = mean_t - slope * mean_s
    if slope < 0:
        slope, fixed = 0.0, mean_t
    elif fixed < 0:
        slope, fixed = sum(s * t for s, t in points) / sum(s * s for s in sizes), 0.0   # through the origin
    residuals = [t - (fixed + slope * s) for s, t in points]
    sd = math.sqrt(sum(r * r for r in residuals) / max(n - 2, 1))
    return Line(fixed, slope, sd)


def allocate(counts: dict, weights: dict, total: int, minimum: int) -> dict:
    """
    How many files to time per type: ``minimum`` each (or all, if fewer), the rest in proportion to bytes.

    The minimum wins over ``total`` when there are many types, because a type with no timings cannot be modelled.
    """
    take = {key: min(count, minimum) for key, count in counts.items()}
    remaining = total - sum(take.values())
    weight_sum = sum(weights.values()) or 1.0
    while remaining > 0:
        open_types = [k for k in counts if take[k] < counts[k]]
        if not open_types:
            break
        neediest = max(open_types, key=lambda k: total * weights[k] / weight_sum - take[k])
        take[neediest] += 1
        remaining -= 1
    return take


def weighted_sample(items: list, weight: Callable, k: int, rng: random.Random) -> list:
    """k distinct items, chosen with probability proportional to ``weight`` (Efraimidis-Spirakis)."""
    keyed = sorted(items, key=lambda item: math.log(1.0 - rng.random()) / weight(item), reverse=True)
    return keyed[:k]


def percentile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    position = q * (len(ordered) - 1)
    low, high = math.floor(position), math.ceil(position)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


# --------------------------------------------------------------------------- #
# Result types
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class TypeModel:
    ext: str
    files: int
    size: int
    timed: int
    line: Line
    basis: str                      # "measured" (every file read), "fitted" or "pooled" (too few of this type)

    @property
    def mb_per_second(self) -> float:
        return 1 / self.line.per_byte / 1024**2 if self.line.per_byte > 0 else math.inf


@dataclass(frozen=True)
class GroupEstimate:
    name: str
    files: int
    size: int
    seconds: float
    low: float
    high: float


@dataclass
class Estimate:
    groups: list
    models: list
    total: GroupEstimate
    interval: float
    walk_seconds: float
    timing_seconds: float
    timed_files: int
    cloud_only_files: int = 0
    notes: list = field(default_factory=list)


@dataclass(frozen=True)
class Item:
    """One file the search would read, and the report line (group) it is counted under."""
    entry: FileEntry
    group: str


@dataclass(frozen=True)
class Census:
    """
    What a population source reports: the files a real run would read.

    ``items`` need only carry a ``FileEntry`` (path, ext, size, ...) and a group name.  ``notes`` are printed with the
    estimate, so an approximate source can say so.  ``seconds`` is how long gathering the census took.
    """
    items: list
    cloud_only: int = 0
    seconds: float = 0.0
    notes: tuple = ()


# A census source: (plan, group_depth) -> Census.  The default walks and stats every file (exact); a different source
# could sample folders and model the rest.  Everything downstream only uses sizes, extensions and groups.
CensusSource = Callable[[SearchPlan, int], Census]


# --------------------------------------------------------------------------- #
# The estimator
# --------------------------------------------------------------------------- #
def _group_name(entry: FileEntry, depth: int) -> str:
    if depth == 0:
        return "(all)"
    parts = [] if entry.relative_dir == "." else entry.relative_dir.split(os.sep)[:depth]
    return os.path.join(entry.top, *parts) if parts else entry.top + " (files directly here)"


def stat_census(plan: SearchPlan, depth: int) -> Census:
    """The default census: walk, and keep the files whose text a real run would read.  Exact, never guesses a size."""
    items, cloud_only, stats, started = [], 0, WalkStats(), time.perf_counter()
    for entry in plan.entries(stats):
        stage = plan.triage(plan.view(entry))
        needs_text = stage is Stage.UNDECIDED or (stage is Stage.MATCHED and plan.scores)
        if not needs_text:
            continue
        if entry.cloud_only and plan.cfg.cloud_only != "read":
            cloud_only += 1                       # reading would download it: never timed, never estimated
            continue
        items.append(Item(entry, _group_name(entry, depth)))
    return Census(items, cloud_only, time.perf_counter() - started)


def _time_sample(plan: SearchPlan, sample: list[Item], max_seconds: float, rng: random.Random,
                 clock: Callable[[], float]) -> tuple[dict, float, bool]:
    """Read the sampled files in random order, timing each.  Returns ({path: seconds}, total, truncated)."""
    order = sample[:]
    rng.shuffle(order)                            # so stopping early does not drop one whole file type
    measured, spent = {}, 0.0
    for item in order:
        if spent >= max_seconds:
            return measured, spent, True
        started = clock()
        try:
            plan.reader(item.entry)
        except Exception as exc:                  # a file that fails to read still cost time; keep the measurement
            logger.debug("Timed read of {} failed: {}", item.entry.path, exc)
        elapsed = clock() - started
        measured[item.entry.path] = elapsed
        spent += elapsed
    return measured, spent, False


def _models(by_ext: dict, measured: dict) -> tuple[dict, Line]:
    """One TypeModel per extension; types with fewer than two timings borrow the pooled line."""
    pooled_points = [(item.entry.size, measured[item.entry.path]) for items in by_ext.values()
                     for item in items if item.entry.path in measured]
    pooled = fit_line(pooled_points)
    models = {}
    for ext, items in by_ext.items():
        points = [(i.entry.size, measured[i.entry.path]) for i in items if i.entry.path in measured]
        total_size = sum(i.entry.size for i in items)
        if len(points) == len(items):
            models[ext] = TypeModel(ext, len(items), total_size, len(points), fit_line(points), "measured")
        elif len(points) >= 2:
            models[ext] = TypeModel(ext, len(items), total_size, len(points), fit_line(points), "fitted")
        else:
            models[ext] = TypeModel(ext, len(items), total_size, len(points), pooled, "pooled")
    return models, pooled


def _predict(by_ext: dict, measured: dict, models: dict) -> tuple[dict, dict]:
    """Point estimate per group: measured seconds for timed files, the type's line for the rest.

    Also returns ``untimed[(group, ext)] = [files, bytes]``, the part of each group that is modelled, not measured."""
    seconds = defaultdict(float)
    untimed = defaultdict(lambda: [0, 0])
    for ext, items in by_ext.items():
        for item in items:
            if item.entry.path in measured:
                seconds[item.group] += measured[item.entry.path]
            else:
                seconds[item.group] += models[ext].line.predict(item.entry.size)
                cell = untimed[(item.group, ext)]
                cell[0] += 1
                cell[1] += item.entry.size
    return seconds, untimed


def _resampled_lines(by_ext: dict, measured: dict, models: dict, pooled_points: list, rng: random.Random) -> dict:
    """One bootstrap round: refit every modelled type's line on timings drawn with replacement."""
    lines = {}
    for ext, model in models.items():
        if model.basis == "measured":
            continue
        points = ([(i.entry.size, measured[i.entry.path]) for i in by_ext[ext] if i.entry.path in measured]
                  if model.basis == "fitted" else pooled_points)
        lines[ext] = fit_line([rng.choice(points) for _ in points]) if points else Line()
    return lines


def _ranges(by_ext: dict, measured: dict, models: dict, pooled_points: list, untimed: dict, measured_by_group: dict,
            interval: float, rng: random.Random) -> tuple[dict, tuple[float, float]]:
    """
    Range of each group's total and of the grand total.

    Every round refits the lines once and uses them for all groups, so the groups' uncertainty moves together and the
    grand-total range is honest (it is not the sum of the groups' ranges).  Each round also adds the noise left over
    after the fit: sd x sqrt(files), since independent errors add as a square root.
    """
    per_group, grand = defaultdict(list), []
    for _ in range(BOOTSTRAP_ROUNDS):
        lines = _resampled_lines(by_ext, measured, models, pooled_points, rng)
        totals = defaultdict(float, measured_by_group)
        for (group, ext), (count, size) in untimed.items():
            line = lines[ext]
            totals[group] += line.fixed * count + line.per_byte * size + rng.gauss(0, models[ext].line.sd * math.sqrt(count))
        for group in {g for g, _ in untimed} | set(measured_by_group):
            per_group[group].append(max(totals[group], measured_by_group.get(group, 0.0)))
        grand.append(max(sum(totals.values()), sum(measured_by_group.values())))
    low_q, high_q = (1 - interval) / 2, 1 - (1 - interval) / 2
    return ({g: (percentile(v, low_q), percentile(v, high_q)) for g, v in per_group.items()},
            (percentile(grand, low_q), percentile(grand, high_q)))


def estimate(cfg, settings: EstimateSettings | None = None, *, reader: Reader | None = None,
             clock: Callable[[], float] = time.perf_counter, census: CensusSource = stat_census) -> Estimate:
    """
    Estimate the reading time of ``cfg``'s search, optionally for one folder (and its subfolders).

    ``reader`` and ``clock`` are injectable so tests can use a synthetic machine with known timings.
    ``census`` says where the list of files comes from (default :func:`stat_census`: a full stat pass); pass another
    :data:`CensusSource` to estimate from a cheaper, approximate population.
    Raises NotADirectoryError for a missing root or folder.
    """
    settings = settings or EstimateSettings()
    if settings.folder:
        cfg = replace(cfg, roots=(settings.folder,), max_depth=None if settings.recursive else 0)
    rng = random.Random(settings.seed)
    plan = SearchPlan.from_settings(cfg, reader)
    found = census(plan, settings.group_depth)
    items, cloud_only, walk_seconds = found.items, found.cloud_only, found.seconds

    by_ext = defaultdict(list)
    for item in items:
        by_ext[item.entry.ext].append(item)
    take = allocate({e: len(v) for e, v in by_ext.items()}, {e: sum(i.entry.size + 1 for i in v) for e, v in by_ext.items()},
                    settings.sample, settings.min_per_type)
    sample = [i for ext, v in by_ext.items() for i in weighted_sample(v, lambda it: it.entry.size + 1, take[ext], rng)]
    logger.info("Estimating {} file(s) to read; timing {} of them", len(items), len(sample))
    measured, timing_seconds, truncated = _time_sample(plan, sample, settings.max_seconds, rng, clock)

    models, _ = _models(by_ext, measured)
    seconds, untimed = _predict(by_ext, measured, models)
    pooled_points = [(i.entry.size, measured[i.entry.path]) for v in by_ext.values() for i in v if i.entry.path in measured]
    measured_by_group = defaultdict(float)
    for i in items:
        if i.entry.path in measured:
            measured_by_group[i.group] += measured[i.entry.path]
    group_ranges, grand_range = (_ranges(by_ext, measured, models, pooled_points, untimed, dict(measured_by_group),
                                         settings.interval, rng) if items else ({}, (0.0, 0.0)))

    members = defaultdict(list)
    for i in items:
        members[i.group].append(i)
    groups = [GroupEstimate(name, len(m), sum(i.entry.size for i in m), seconds[name], *group_ranges[name])
              for name, m in sorted(members.items())]
    total = GroupEstimate("TOTAL", len(items), sum(i.entry.size for i in items), sum(g.seconds for g in groups), *grand_range)

    notes = [*found.notes, "Assumes files are read one at a time, as a real run does.",
             "Does not include the folder listing (walk), SHA-256 hashing or writing the CSV files."]
    if truncated:
        notes.append(f"Timing stopped at the {settings.max_seconds:g} s limit with {len(measured)} of {len(sample)} files timed.")
    if cloud_only:
        notes.append(f"{cloud_only} online-only file(s) were not estimated: reading them would download them "
                     f"(cloud_only={plan.cfg.cloud_only}).")
    if any(m.basis == "pooled" for m in models.values()):
        notes.append("File types marked 'pooled' had too few timings of their own, so the average over all types was used.")
    return Estimate(groups, sorted(models.values(), key=lambda m: -m.size), total, settings.interval, walk_seconds,
                    timing_seconds, len(measured), cloud_only, notes)


# --------------------------------------------------------------------------- #
# Report
# --------------------------------------------------------------------------- #
def format_estimate(est: Estimate) -> str:
    """The estimate as a plain-text table, plus what it is based on and what it leaves out."""
    pct = round(est.interval * 100)
    head = f"{'Folder':<48} {'Files':>8} {'Size':>10} {'Estimated':>12}   {pct}% range"
    lines = ["Time to read the text of the files a run would read", head, "-" * len(head)]
    for group in [*est.groups, est.total] if len(est.groups) > 1 else [est.total]:
        label = group.name if len(group.name) <= 48 else "..." + group.name[-45:]
        lines.append(f"{label:<48} {group.files:>8} {format_size(group.size):>10} {format_duration(group.seconds):>12}   "
                     f"{format_duration(group.low)} to {format_duration(group.high)}")
    lines += ["", f"Based on timing {est.timed_files} real reads ({format_duration(est.timing_seconds)}); "
                  f"the folder listing took {format_duration(est.walk_seconds)}.", "", "Speed by file type:"]
    for m in est.models[:8]:
        rate = "n/a" if math.isinf(m.mb_per_second) else f"{m.mb_per_second:.1f} MB/s"
        lines.append(f"  .{m.ext or '(none)':<8} {m.files:>7} files {format_size(m.size):>10}  "
                     f"{m.line.fixed * 1000:6.0f} ms per file + {rate}  [{m.basis}, {m.timed} timed]")
    lines += ["", *[f"Note: {n}" for n in est.notes]]
    return "\n".join(lines)
