"""The news agent: six stages, in order, over a batch of articles.

    ingest -> dedupe -> entity link -> classify -> score credibility -> impact

Two of the six are LLM calls. The other four are deterministic domain code, and
that split is the point: classification and impact are judgements, while
grouping, entity resolution and credibility are arithmetic that should give the
same answer every time it runs.

**Stage order is a cost decision as much as a correctness one.** Deduping
*before* classifying means five outlets carrying one announcement cost one
model call rather than five. Entity linking before classifying means an article
naming nothing in the universe is dropped before anything is paid for. On a
typical day that is the difference between a few hundred calls and a few
thousand.

**The universe arrives with the run, and an empty one raises.** ISS-035: the agent used
to check only whether *some* symbol resolved, which is always true for a filing
because the exchange names the company. The archive holds 804 companies against a
26-name universe, so ~97% of the spend bought classifications of names that could
never be traded. The filter now compares against the universe itself, and runs
before dedup.

**A dropped article is counted, never silently discarded.** `PipelineReport`
carries a reason tally, because "the briefing had no ideas" and "the entity
linker matched nothing all day" look identical from the outside otherwise
(CLAUDE.md rule 6).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from trading_desk.agents.news.catalyst_classifier import CatalystClassifier, ClassifiedArticle
from trading_desk.agents.news.impact_analyst import ImpactAnalyst, ImpactView
from trading_desk.domain.credibility import CredibilityScore, score_credibility
from trading_desk.domain.entities import AliasTable, resolve_entities
from trading_desk.domain.news import NewsArticle
from trading_desk.domain.similarity import DEFAULT_THRESHOLD, SimilarDocument, group_near_duplicates


@dataclass(frozen=True)
class Catalyst:
    """One classified, scored, entity-linked story. The agent's output.

    `corroborating_publishers` comes from the dedup group, so a story carried
    by five outlets scores once with the corroboration counted - rather than
    five times, which would read as five independent confirmations.
    """

    article: NewsArticle
    symbol: str
    classified: ClassifiedArticle
    credibility: CredibilityScore
    impact: ImpactView
    corroborating_publishers: int
    also_reported_by: tuple[str, ...]


@dataclass(frozen=True)
class PipelineReport:
    """What the run produced and, as importantly, what it dropped and why."""

    catalysts: list[Catalyst] = field(default_factory=list)
    dropped: dict[str, int] = field(default_factory=dict)
    suspicious_articles: list[str] = field(default_factory=list)
    articles_in: int = 0
    stories_after_dedup: int = 0
    llm_calls: int = 0

    def as_lines(self) -> list[str]:
        lines = [
            f"{self.articles_in} articles in, {self.stories_after_dedup} distinct stories, "
            f"{len(self.catalysts)} catalysts out ({self.llm_calls} model calls)"
        ]
        for reason, count in sorted(self.dropped.items(), key=lambda item: -item[1]):
            lines.append(f"  dropped {count:>4}  {reason}")
        if self.suspicious_articles:
            lines.append(f"  FLAGGED {len(self.suspicious_articles)} article(s) for injection")
        return lines


class NewsAgent:
    def __init__(
        self,
        classifier: CatalystClassifier,
        analyst: ImpactAnalyst,
        aliases: AliasTable,
        similarity_threshold: float = DEFAULT_THRESHOLD,
    ) -> None:
        self._classifier = classifier
        self._analyst = analyst
        self._aliases = aliases
        self._threshold = similarity_threshold

    def run(
        self, articles: list[NewsArticle], cycle_id: str, universe: frozenset[str]
    ) -> PipelineReport:
        """Catalysts for `universe`, and a tally of everything dropped.

        `universe` is a parameter rather than constructor state because
        `CycleRequest` already carries the day's universe. Holding a second copy
        here would let the two drift, and a stale copy would silently classify
        names we no longer trade.
        """
        if not universe:
            raise ValueError(
                "news pipeline needs a universe; an empty one drops every article "
                "and would read as a quiet news day (ISS-035, CLAUDE.md rule 6)"
            )

        dropped: dict[str, int] = {}
        suspicious: list[str] = []
        catalysts: list[Catalyst] = []
        calls = 0

        # Before grouping, so a non-universe story is not even a dedup candidate
        # and certainly never reaches a paid call.
        relevant = self._only_in_universe(articles, universe, dropped)

        groups = self._group(relevant)
        by_id = {article.article_id: article for article in relevant}

        for representative_id, member_ids in groups:
            article = by_id[representative_id]
            symbols = self._universe_symbols(article, universe)
            if not symbols:  # pragma: no cover - _only_in_universe already dropped these
                _count(dropped, "no universe instrument named")
                continue

            classified = self._classifier.classify(article, cycle_id)
            calls += 1
            if classified.is_suspicious:
                suspicious.append(article.article_id)
            if not classified.quote_was_grounded:
                _count(dropped, "evidence quote not found in the article")
                continue
            if not classified.label.is_tradable:
                _count(dropped, "not a catalyst")
                continue

            others = tuple(
                sorted({by_id[member].publisher for member in member_ids} - {article.publisher})
            )
            credibility = score_credibility(
                headline=article.headline,
                body=article.body,
                source_tier=article.source_tier,
                corroborating_publishers=len(others),
            )

            # One impact call per (story, symbol): an article naming two
            # companies is two different questions with two different answers.
            for symbol in symbols:
                impact = self._analyst.assess(article, symbol, classified.label, cycle_id)
                calls += 1
                catalysts.append(
                    Catalyst(
                        article=article,
                        symbol=symbol,
                        classified=classified,
                        credibility=credibility,
                        impact=impact,
                        corroborating_publishers=len(others),
                        also_reported_by=others,
                    )
                )

        return PipelineReport(
            catalysts=catalysts,
            dropped=dropped,
            suspicious_articles=suspicious,
            articles_in=len(articles),
            stories_after_dedup=len(groups),
            llm_calls=calls,
        )

    def _group(self, articles: list[NewsArticle]) -> list[tuple[str, tuple[str, ...]]]:
        documents = [
            SimilarDocument(
                document_id=article.article_id,
                text=f"{article.headline} {article.body}",
                symbols=frozenset(self._symbols_for(article)),
            )
            for article in articles
        ]
        groups = group_near_duplicates(documents, threshold=self._threshold)
        return [(group.representative_id, group.member_ids) for group in groups]

    def _only_in_universe(
        self, articles: list[NewsArticle], universe: frozenset[str], dropped: dict[str, int]
    ) -> list[NewsArticle]:
        """Articles naming at least one instrument we can actually trade.

        ISS-035: this check used to be `if not symbols` further down, which asks
        "did we resolve any symbol" rather than "is it one of ours". A filing
        arrives from the exchange with its company already identified, so that
        test never failed for a filing and every company on the exchange got
        classified at our expense - 804 of them against a 26-name universe.
        """
        kept = []
        for article in articles:
            if self._universe_symbols(article, universe):
                kept.append(article)
            else:
                _count(dropped, "no universe instrument named")
        return kept

    def _universe_symbols(self, article: NewsArticle, universe: frozenset[str]) -> list[str]:
        """The article's symbols, narrowed to the universe.

        Narrowing matters in the impact loop too: an article naming one of our
        names and three others would otherwise buy an impact call for each.
        """
        return sorted(set(self._symbols_for(article)) & universe)

    def _symbols_for(self, article: NewsArticle) -> list[str]:
        """Symbols the article already carried, else whatever the aliases find.

        Trusting a symbol the ingest attached is not laziness: filings arrive
        with the company already identified by the exchange, which is better
        evidence than any text match.
        """
        if article.instrument_symbols:
            return list(article.instrument_symbols)
        matches = resolve_entities(f"{article.headline} {article.body}", self._aliases)
        return sorted({match.symbol for match in matches})


def _count(tally: dict[str, int], reason: str) -> None:
    tally[reason] = tally.get(reason, 0) + 1
