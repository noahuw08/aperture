"""Offline replay of collected sessions.

Shadow mode logs *context* and *what was called*, not a decision from any particular
engine. That is deliberate: bake one selector into collection and you can only ever
evaluate that selector. Log enough and the counterfactual for **any** engine can be
computed after the fact, from the same sessions, including engines that did not exist
when the data was collected.

This package is that computation. Give it a selector, it walks the real session log in
chronological order, shows the selector only what preceded each session, and scores the
set it chose against what the session actually went on to call.
"""
