"""The Phase 2 data plane: an MCP proxy that selects which tools to expose.

Transport only. All ranking and budget logic lives in the selection library one
package up (``catalog``, ``selector``, ``baselines``, ``tokens``) and is consumed
here unchanged.
"""
