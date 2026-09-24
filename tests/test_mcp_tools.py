"""
Unit tests for the four MCP tools in mcp_server.py.

These run against the real, already-ingested data/index/chunks.json that's
checked into the repo (the three public AIG filings), rather than a synthetic
fixture -- that index is small, stable, and lets these tests also double as a
sanity check that ingestion produced usable content. Assertions are written
generically (structure and invariants, not exact snippets) so they don't
break if the source PDFs are re-ingested.

@mcp.tool()-decorated functions in the official MCP Python SDK's FastMCP
remain plain, directly-callable Python functions (the decorator registers
them as a side effect) -- that's what makes calling them straight from a
test, with no MCP client/transport involved, both possible and the
documented way to unit test them.
"""
import mcp_server


class TestListDocuments:
    def test_returns_the_ingested_manifest(self):
        docs = mcp_server.list_documents()
        assert isinstance(docs, list)
        assert len(docs) == 3  # the three AIG filings in data/raw/
        for doc in docs:
            assert doc["doc_id"]
            assert doc["num_pages"] > 0


class TestSearchDocuments:
    def test_returns_ranked_grounded_results(self):
        # "AIG" appears in the copyright line of every ingested page, so this
        # is a safe, always-present term to assert against.
        results = mcp_server.search_documents("AIG", top_k=5)
        assert isinstance(results, list)
        assert len(results) > 0
        for r in results:
            assert set(r.keys()) == {"doc_id", "page", "snippet", "score"}
            assert r["score"] > 0
            assert r["page"] >= 1

    def test_results_are_sorted_by_score_descending(self):
        results = mcp_server.search_documents("AIG", top_k=10)
        scores = [r["score"] for r in results]
        assert scores == sorted(scores, reverse=True)

    def test_respects_top_k(self):
        results = mcp_server.search_documents("AIG", top_k=2)
        assert len(results) <= 2

    def test_nonsense_query_returns_no_results(self):
        results = mcp_server.search_documents("zzqxw9nonexistentgibberishterm")
        assert results == []


class TestGetPageText:
    def test_returns_text_for_a_real_page(self):
        doc_id = mcp_server.list_documents()[0]["doc_id"]
        text = mcp_server.get_page_text(doc_id, 1)
        assert isinstance(text, str)
        assert len(text) > 0
        assert not text.startswith("No text found")

    def test_returns_a_not_found_message_for_a_missing_page(self):
        doc_id = mcp_server.list_documents()[0]["doc_id"]
        text = mcp_server.get_page_text(doc_id, 99999)
        assert text == f"No text found for {doc_id} page 99999."

    def test_returns_a_not_found_message_for_an_unknown_document(self):
        text = mcp_server.get_page_text("not-a-real-doc-id", 1)
        assert text.startswith("No text found")


class TestExtractFinancialMetrics:
    def test_finds_dollar_amounts_and_percentages(self):
        text = (
            "Net premiums written were $1.2 billion, up 5.3% year over year, "
            "compared to $980 million a year ago."
        )
        result = mcp_server.extract_financial_metrics(text)
        assert result["candidate_count"] == len(result["candidates"])
        assert result["candidate_count"] >= 3
        types = {c["type"] for c in result["candidates"]}
        assert types == {"dollar_amount", "percentage"}
        for c in result["candidates"]:
            assert c["value"] in c["context"]

    def test_no_false_positives_on_plain_text(self):
        result = mcp_server.extract_financial_metrics(
            "This paragraph describes the business but contains no figures."
        )
        assert result == {"candidate_count": 0, "candidates": []}
