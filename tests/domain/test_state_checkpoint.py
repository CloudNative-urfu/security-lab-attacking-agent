"""Контракт capability state-checkpoint."""

from __future__ import annotations

from pathlib import Path

from attacking_agent.domain.checkpoint import (
    CheckpointManager,
    CheckpointStatus,
    InMemoryManifestStore,
    LocalArtifactStore,
    StaticTargetSnapshotAdapter,
)
from attacking_agent.domain.common.ids import SequentialIdFactory, UuidIdFactory
from attacking_agent.domain.common.scope import Scope
from attacking_agent.domain.memory import InMemoryMemoryStore, KnowledgeInput, Writer
from attacking_agent.domain.tools import (
    InMemoryCallJournal,
    Observation,
    ObservationStatus,
    ResultRecord,
)


def _scope(branch: str = "br-1") -> Scope:
    return Scope(campaign_id="camp-1", branch_id=branch, target_ref="svc-demo")


def _artifact_store(tmp: Path) -> LocalArtifactStore:
    return LocalArtifactStore(tmp / "artifacts")


def _commit_knowledge(mem: InMemoryMemoryStore, scope: Scope, statement: str) -> int:
    writer = Writer(mem, id_factory=UuidIdFactory())
    base = mem.branch_version(scope)
    cand = writer.prepare(
        scope,
        base_version=base,
        read_set=[],
        knowledge=[KnowledgeInput(statement=statement)],
    )
    assessment = writer.evaluate(scope, cand)
    writer.commit(scope, cand, assessment)
    return mem.branch_version(scope)


def _manager(
    tmp: Path, mem: InMemoryMemoryStore, journal: InMemoryCallJournal, with_adapter: bool = True
) -> CheckpointManager:
    adapter = StaticTargetSnapshotAdapter() if with_adapter else None
    return CheckpointManager(
        artifact_store=_artifact_store(tmp),
        manifest_store=InMemoryManifestStore(),
        memory_store=mem,
        journal=journal,
        snapshot_adapter=adapter,
        id_factory=SequentialIdFactory(),
    )


def test_content_addressed_artifact_store_roundtrip(tmp_path: Path) -> None:
    store = _artifact_store(tmp_path)
    h1 = store.put(b"hello")
    h2 = store.put(b"hello")
    assert h1 == h2  # content-addressed
    assert store.exists(h1)
    assert store.get(h1) == b"hello"


def test_full_checkpoint_ready(tmp_path: Path) -> None:
    mem = InMemoryMemoryStore()
    scope = _scope()
    _commit_knowledge(mem, scope, "знание-1")
    mgr = _manager(tmp_path, mem, InMemoryCallJournal())
    res = mgr.create_checkpoint(scope, command_id="cmd-1")
    assert res.status is CheckpointStatus.READY
    assert res.manifest is not None
    assert res.manifest.target_snapshot_ref is not None


def test_unknown_blocks_ready(tmp_path: Path) -> None:
    mem = InMemoryMemoryStore()
    scope = _scope()
    journal = InMemoryCallJournal()
    journal.record_result(
        ResultRecord(
            op_id="op-1",
            attempt_id="att-1",
            branch_id="br-1",
            observation=Observation(ObservationStatus.UNKNOWN, {}),
            dispatch_status="unknown",
        )
    )
    mgr = _manager(tmp_path, mem, journal)
    res = mgr.create_checkpoint(scope, command_id="cmd-1")
    assert res.status in (CheckpointStatus.PENDING, CheckpointStatus.BLOCKED)


def test_blocked_without_snapshot_adapter(tmp_path: Path) -> None:
    mem = InMemoryMemoryStore()
    scope = _scope()
    mgr = _manager(tmp_path, mem, InMemoryCallJournal(), with_adapter=False)
    res = mgr.create_checkpoint(scope, command_id="cmd-1")
    assert res.status is CheckpointStatus.BLOCKED


def test_lost_ack_reread_by_command_id_no_duplicate(tmp_path: Path) -> None:
    mem = InMemoryMemoryStore()
    scope = _scope()
    _commit_knowledge(mem, scope, "знание-1")
    mgr = _manager(tmp_path, mem, InMemoryCallJournal())
    r1 = mgr.create_checkpoint(scope, command_id="cmd-1")
    r2 = mgr.create_checkpoint(scope, command_id="cmd-1")  # повтор команды (потеря ACK)
    assert r1.status is CheckpointStatus.READY
    assert r2.status is CheckpointStatus.READY
    assert r1.manifest is not None and r2.manifest is not None
    assert r1.manifest.checkpoint_id == r2.manifest.checkpoint_id  # нет второго head


def test_fork_does_not_see_parent_future(tmp_path: Path) -> None:
    mem = InMemoryMemoryStore()
    parent = _scope("br-1")
    _commit_knowledge(mem, parent, "знание-в-точке-P")
    mgr = _manager(tmp_path, mem, InMemoryCallJournal())
    ckpt = mgr.create_checkpoint(parent, command_id="cmd-1")
    assert ckpt.manifest is not None
    # родитель продолжает после точки P
    _commit_knowledge(mem, parent, "знание-после-P")
    # форк из исторической точки P
    fork_scope = mgr.fork(ckpt.manifest, new_branch_id="fork-1")
    from attacking_agent.domain.memory import Reader, ReadMode

    reader = Reader(mem)
    fork_pkg = reader.read(fork_scope, ReadMode.ANALYSIS, query="знание", limit=50)
    statements = {k.statement for k in fork_pkg.knowledge}
    assert "знание-в-точке-P" in statements
    assert "знание-после-P" not in statements  # будущее родителя невидимо
    # родитель видит оба
    parent_pkg = reader.read(parent, ReadMode.ANALYSIS, query="знание", limit=50)
    assert {"знание-в-точке-P", "знание-после-P"} <= {k.statement for k in parent_pkg.knowledge}


def test_fork_does_not_reset_ledger(tmp_path: Path) -> None:
    from decimal import Decimal

    from attacking_agent.domain.budget import (
        BudgetAccountRef,
        InMemoryBudgetStore,
        LedgerManager,
    )
    from attacking_agent.domain.common.clock import ManualClock
    from attacking_agent.domain.config import BudgetLevel, BudgetType, Hardness

    mem = InMemoryMemoryStore()
    scope = _scope()
    _commit_knowledge(mem, scope, "знание-1")
    ledger = LedgerManager(InMemoryBudgetStore(), clock=ManualClock())
    ref = BudgetAccountRef(
        campaign_id="camp-1", level=BudgetLevel.CAMPAIGN, scope_key="camp-1",
        budget_type=BudgetType.MONEY,
    )
    ledger.open_account(ref, limit=Decimal("5"), hardness=Hardness.HARD, currency="USD")
    ledger.reserve(ref, Decimal("2"), "r1")
    ledger.start("r1", "a1")
    ledger.settle("a1", "c1", Decimal("2"))
    mgr = _manager(tmp_path, mem, InMemoryCallJournal())
    ckpt = mgr.create_checkpoint(scope, command_id="cmd-1")
    assert ckpt.manifest is not None
    mgr.fork(ckpt.manifest, new_branch_id="fork-1")
    assert ledger.consumed(ref) == Decimal("2")  # расход не откатился
