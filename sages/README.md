# Agent Harness Workspace

This directory is reserved for local checkouts, experiments, and reference implementations of agent harness frameworks. Those framework sources are intentionally excluded from the MAGI Memo repository and should be managed in their own repositories.

The only integration code retained here is [`dsh/magi-memo`](dsh/magi-memo/), the MAGI Memo plugin for DeepSeek Harness. A local DSH checkout may still be placed under `sages/dsh/`; Git ignores the harness itself while keeping the plugin visible.

Standalone projects developed here, such as MAGI Harness, should be moved to their own repository rather than committed to MAGI Memo.
