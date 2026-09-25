"""Re-exports the analysis skill definitions and prompt builder from
harness.prompts.analysis_prompts, kept as the stable import path used
elsewhere in the harness."""

from __future__ import annotations

from harness.prompts.analysis_prompts import SKILLS, build_analysis_prompt

__all__ = ["SKILLS", "build_analysis_prompt"]