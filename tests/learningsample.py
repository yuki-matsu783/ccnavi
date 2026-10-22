"""学びの蓄積のテストが共有する、パターンと採否の記録の文面。"""

from __future__ import annotations


def pattern(sources, target="docs"):
    listed = ", ".join(sources)
    return (
        f"---\ntype: learning-wiki\ntarget: {target}\nsources: [{listed}]\n"
        "title: t\ndescription: d\ntags: [a]\nkeywords: [b]\n---\n\n本文\n"
    )


def impact(proposal, status, patterns, decided="2026-01-01T00:00:00+00:00", target="docs"):
    counts = "".join(f"  {k}: {v}\n" for k, v in patterns.items())
    return (
        f"---\nproposal: {proposal}\ntarget: {target}\nfiles: [a.md]\npatterns:\n{counts}"
        f"status: {status}\nreason: r\ndecided_at: '{decided}'\n---\n\n差分\n"
    )
