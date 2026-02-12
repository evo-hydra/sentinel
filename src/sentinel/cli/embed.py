"""sentinel embed — generate embeddings for semantic search."""

from __future__ import annotations

from pathlib import Path

import typer

from sentinel.cli import theme


def embed(
    provider: str | None = typer.Option(None, "--provider", help="Embedding provider (ollama, openai)."),
    model: str | None = typer.Option(None, "--model", help="Embedding model name override."),
    batch_size: int | None = typer.Option(None, "--batch-size", help="Texts per embedding batch."),
    ktype: str | None = typer.Option(None, "--type", "-t", help="Only embed this type (convention/decision/pitfall/pattern)."),
    force: bool = typer.Option(False, "--force", help="Re-embed entries that already have embeddings."),
) -> None:
    """Generate embeddings for knowledge entries to enable semantic search."""
    from sentinel.core.config import SentinelConfig
    from sentinel.core.embedding_provider import EmbeddingProviderError
    from sentinel.core.git import find_git_root
    from sentinel.core.knowledge import KnowledgeStore
    from sentinel.core.provider_factory import create_embedding_provider
    from sentinel.models.enums import KnowledgeType

    cwd = Path.cwd()
    git_root = find_git_root(cwd)
    if git_root is None:
        theme.error("Not in a git repository.")
        raise typer.Exit(1)

    sentinel_dir = git_root / ".sentinel"
    if not sentinel_dir.exists():
        theme.error("Sentinel not initialized. Run: sentinel init")
        raise typer.Exit(1)

    config = SentinelConfig.load(sentinel_dir)
    if provider:
        config.embed_provider = provider
    if model:
        config.embed_model = model
    if batch_size:
        config.embed_batch_size = batch_size

    theme.banner()

    try:
        embed_provider = create_embedding_provider(config)
    except EmbeddingProviderError as e:
        theme.error(f"Failed to create embedding provider: {e}")
        raise typer.Exit(1) from None

    store = KnowledgeStore(sentinel_dir / "sentinel.db")
    with store:
        # Collect entries to embed
        entries: list[tuple[str, str, str]] = []  # (id, type, text)

        type_filter = KnowledgeType(ktype) if ktype else None

        if type_filter is None or type_filter == KnowledgeType.CONVENTION:
            for c in store.get_conventions(limit=10000):
                entries.append((c.id, "convention", f"{c.pattern} {c.description}"))

        if type_filter is None or type_filter == KnowledgeType.DECISION:
            for d in store.get_decisions(limit=10000):
                entries.append((d.id, "decision", f"{d.summary} {d.rationale}"))

        if type_filter is None or type_filter == KnowledgeType.PITFALL:
            for pit in store.get_pitfalls(limit=10000):
                entries.append((pit.id, "pitfall", f"{pit.description} {pit.how_to_prevent}"))

        if type_filter is None or type_filter == KnowledgeType.PATTERN:
            for pat in store.get_patterns(limit=10000):
                entries.append((pat.id, "pattern", f"{pat.name} {pat.description}"))

        if not entries:
            theme.info("No knowledge entries to embed.")
            raise typer.Exit(0)

        # Filter out already-embedded entries (unless --force)
        if not force:
            existing = {
                row[0]
                for row in store.conn.execute("SELECT knowledge_id FROM embeddings").fetchall()
            }
            entries = [(eid, etype, text) for eid, etype, text in entries if eid not in existing]

        if not entries:
            theme.info("All entries already have embeddings. Use --force to re-embed.")
            raise typer.Exit(0)

        theme.info(
            f"Embedding {len(entries)} entries with "
            f"{config.embed_provider}/{config.embed_model}..."
        )

        # Process in batches
        bs = config.embed_batch_size
        total_batches = (len(entries) + bs - 1) // bs
        embedded = 0

        for i in range(0, len(entries), bs):
            batch = entries[i: i + bs]
            texts = [text for _, _, text in batch]

            try:
                vectors = embed_provider.embed_batch(texts)
            except EmbeddingProviderError as e:
                theme.error(f"Embedding batch failed: {e}")
                raise typer.Exit(1) from None

            for (eid, etype, _), vec in zip(batch, vectors, strict=True):
                store.store_embedding(eid, etype, vec, embed_provider.model_name)

            embedded += len(batch)
            batch_num = i // bs + 1
            theme.muted(f"  Batch {batch_num}/{total_batches} complete ({embedded}/{len(entries)})")

    theme.success(f"Embedded {embedded} knowledge entries.")
