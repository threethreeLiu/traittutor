"""Built-in capability class paths."""

BUILTIN_CAPABILITY_CLASSES: dict[str, str] = {
    "chat": "traittutor.agents.chat.capability:ChatCapability",
    "deep_solve": "traittutor.capabilities.solve.capability:DeepSolveCapability",
    "deep_question": "traittutor.agents.question.capability:DeepQuestionCapability",
    "deep_research": "traittutor.agents.research.capability:DeepResearchCapability",
    "math_animator": "traittutor.agents.math_animator.capability:MathAnimatorCapability",
    "visualize": "traittutor.agents.visualize.capability:VisualizeCapability",
    "mastery_path": "traittutor.capabilities.mastery.capability:MasteryPathCapability",
}
