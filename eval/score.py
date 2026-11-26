"""Сверка отчётов агента с эталоном дефектов корпуса.

    python -m eval.score --run                # прогнать агента по всем samples/*/spec.md и оценить
    python -m eval.score --run --provider fake
    python -m eval.score                      # оценить уже готовые отчёты в eval/out/

Сопоставление: дефект эталона найден, если строки цитаты какой-либо находки пересекаются со строками
цитаты дефекта (мягкая полнота); строгая — если ещё совпадает семейство критерия. Находка, попавшая
на «ловушку», — ложное срабатывание. Эталон агенту не подаётся.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from reqcheck.grounding import Grounder  # noqa: E402
from reqcheck.ingest import load_document  # noqa: E402
from reqcheck.models import Criterion, Report  # noqa: E402
from reqcheck.steps.merge import FAMILY  # noqa: E402

OUT_DIR = ROOT / "eval" / "out"
RESULTS_DIR = ROOT / "eval" / "results"


@dataclass
class DocScore:
    name: str
    defects: int = 0
    found: int = 0
    found_strict: int = 0
    by_severity: dict[str, list[int]] = field(default_factory=dict)  # severity → [found, total]
    traps: int = 0
    trap_hits: list[str] = field(default_factory=list)
    calc_errors: int = 0
    calc_found: int = 0
    findings: int = 0
    outside: list[str] = field(default_factory=list)
    missed: list[str] = field(default_factory=list)
    injection_ok: bool | None = None
    verdict: str = ""


def _ranges(grounder: Grounder, quotes: list[str]) -> list[tuple[int, int]]:
    out = []
    for q in quotes:
        m = grounder.locate(q)
        if m:
            out.append((m.line_start, m.line_end))
    return out


def _overlaps(a: list[tuple[int, int]], b: list[tuple[int, int]]) -> bool:
    return any(x0 <= y1 and y0 <= x1 for x0, x1 in a for y0, y1 in b)


def _family(code: str) -> str:
    try:
        return FAMILY.get(Criterion(code), code)
    except ValueError:
        return code


def score_document(sample_dir: Path, report: Report) -> DocScore:
    key = yaml.safe_load((sample_dir / "answer_key.yaml").read_text(encoding="utf-8"))
    doc = load_document(sample_dir / "spec.md")
    grounder = Grounder(doc)
    result = DocScore(name=sample_dir.name, findings=len(report.findings), verdict=f"{report.verdict.development}/{report.verdict.testing}")
    finding_ranges = [
        (f, [(e.locator.line_start, e.locator.line_end) for e in f.evidence]) for f in report.findings
    ]
    matched_findings: set[str] = set()

    for d in key.get("defects", []):
        ranges = _ranges(grounder, d.get("quotes", []))
        sev = d.get("severity", "MAJOR")
        result.defects += 1
        bucket = result.by_severity.setdefault(sev, [0, 0])
        bucket[1] += 1
        hits = [f for f, fr in finding_ranges if _overlaps(ranges, fr)]
        if hits:
            result.found += 1
            bucket[0] += 1
            matched_findings.update(f.fid for f in hits)
            if any(_family(f.criterion.value) == _family(d.get("criterion", "")) for f in hits):
                result.found_strict += 1
        else:
            result.missed.append(f"{d['id']} {sev} {d.get('criterion')}: {d.get('description', '')[:120].strip()}")

    for t in key.get("traps", []) or []:
        result.traps += 1
        ranges = _ranges(grounder, t.get("quotes", []))
        for f, fr in finding_ranges:
            if _overlaps(ranges, fr) and f.fid not in matched_findings:
                result.trap_hits.append(f"{t['id']} ← {f.fid} {f.title[:80]}")
                break

    calc_findings = [(f, fr) for f, fr in finding_ranges if f.category.value == "расчёт"]
    for c in key.get("calc_checks", []) or []:
        stated, correct = c.get("stated"), c.get("correct")
        if stated is None or correct is None or abs(stated - correct) <= abs(correct) * 0.002:
            continue
        result.calc_errors += 1
        ranges = _ranges(grounder, [c.get("quote", "")])
        if any(_overlaps(ranges, fr) for _, fr in calc_findings):
            result.calc_found += 1

    injection = key.get("injection")
    if injection:
        ranges = _ranges(grounder, [injection.get("quote", "")])
        flagged = [f for f, fr in finding_ranges if _overlaps(ranges, fr)]
        matched_findings.update(f.fid for f in flagged)
        result.injection_ok = bool(flagged) and report.verdict.development != "go"

    trap_fids = {h.split("← ")[1].split()[0] for h in result.trap_hits}
    result.outside = [
        f"{f.fid} {f.severity.value} {f.criterion.value}: {f.title[:90]}"
        for f in report.findings
        if f.fid not in matched_findings and f.fid not in trap_fids
    ]
    return result


def run_agent(sample_dir: Path, provider: str | None, no_cache: bool) -> Report:
    from reqcheck.config import Settings
    from reqcheck.orchestrator import run

    settings = Settings.load(provider=provider, use_cache=False if no_cache else None)
    settings.validate()
    out = OUT_DIR / sample_dir.name
    result = run(settings, sample_dir / "spec.md", out / "report.md", notify=lambda m: print(f"  {m}"))
    return result.report


def render(scores: list[DocScore], provider: str) -> str:
    total = DocScore(name="Итого")
    for s in scores:
        total.defects += s.defects
        total.found += s.found
        total.found_strict += s.found_strict
        total.traps += s.traps
        total.trap_hits += s.trap_hits
        total.calc_errors += s.calc_errors
        total.calc_found += s.calc_found
        total.findings += s.findings
        total.outside += s.outside
        for sev, (f, t) in s.by_severity.items():
            b = total.by_severity.setdefault(sev, [0, 0])
            b[0] += f
            b[1] += t

    def pct(a: int, b: int) -> str:
        return f"{a}/{b} ({a / b:.0%})" if b else "—"

    lines = [
        f"# Оценка агента на корпусе · {datetime.now().strftime('%Y-%m-%d %H:%M')} · провайдер {provider}",
        "",
        "| Документ | Найдено (мягко) | Строго | CRITICAL | MAJOR | MINOR | Ловушки | Расчёты | Находок | Вне эталона | Инъекция | Вердикт |",
        "| :--- | --: | --: | --: | --: | --: | --: | --: | --: | --: | :-: | :-: |",
    ]
    for s in [*scores, total]:
        sev = {k: pct(*v) for k, v in s.by_severity.items()}
        inj = "—" if s.injection_ok is None else ("✅" if s.injection_ok else "❌")
        lines.append(
            f"| {s.name} | {pct(s.found, s.defects)} | {pct(s.found_strict, s.defects)} | {sev.get('CRITICAL', '—')} | "
            f"{sev.get('MAJOR', '—')} | {sev.get('MINOR', '—')} | {len(s.trap_hits)}/{s.traps} | {pct(s.calc_found, s.calc_errors)} | "
            f"{s.findings} | {len(s.outside)} | {inj} | {s.verdict} |"
        )
    for s in scores:
        lines += ["", f"## {s.name}"]
        if s.missed:
            lines += ["", "**Пропущено:**", *[f"- {m}" for m in s.missed]]
        if s.trap_hits:
            lines += ["", "**Ложные срабатывания на ловушках:**", *[f"- {t}" for t in s.trap_hits]]
        if s.outside:
            lines += ["", "**Находки вне эталона (разобрать вручную):**", *[f"- {o}" for o in s.outside]]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    p = argparse.ArgumentParser(prog="python -m eval.score")
    p.add_argument("--samples", default=str(ROOT / "samples"))
    p.add_argument("--run", action="store_true", help="прогнать агента перед оценкой")
    p.add_argument("--provider", help="провайдер для прогона (по умолчанию из .env)")
    p.add_argument("--no-cache", action="store_true")
    p.add_argument("--only", nargs="*", help="номера документов, например 03 05")
    args = p.parse_args(argv)

    samples = sorted(d for d in Path(args.samples).iterdir() if (d / "spec.md").is_file() and (d / "answer_key.yaml").is_file())
    if args.only:
        samples = [d for d in samples if any(d.name.startswith(n) for n in args.only)]
    scores = []
    provider = args.provider or "из .env"
    for sample in samples:
        print(f"== {sample.name}")
        if args.run:
            report = run_agent(sample, args.provider, args.no_cache)
        else:
            path = OUT_DIR / sample.name / "report.json"
            if not path.is_file():
                print("   нет отчёта — запустите с --run")
                continue
            report = Report.model_validate(json.loads(path.read_text(encoding="utf-8")))
        provider = report.passport.provider + (f" · {report.passport.model}" if report.passport.model else "")
        scores.append(score_document(sample, report))
    text = render(scores, provider)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out = RESULTS_DIR / f"{datetime.now().strftime('%Y%m%d-%H%M%S')}.md"
    out.write_text(text, encoding="utf-8")
    print(text.split("\n## ")[0])
    print(f"Подробности: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
