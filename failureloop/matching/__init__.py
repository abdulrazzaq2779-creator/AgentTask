"""Pattern matching: recall + explainable similarity scoring."""

from failureloop.matching.matcher import MatchResult, find_similar_incidents
from failureloop.matching.fix_ranker import RankedFixes, rank_fixes

__all__ = ["MatchResult", "find_similar_incidents", "RankedFixes", "rank_fixes"]
