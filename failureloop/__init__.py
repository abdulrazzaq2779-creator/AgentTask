"""FailureLoop — AI incident recurrence prevention agent.

Remembers past incidents as structured "failure fingerprints", matches new
incidents against that memory by underlying failure pattern (not error text),
and recommends fixes proven to work while warning about fixes that failed.
"""

__version__ = "0.1.0"
