"""Built-in capability class paths."""

BUILTIN_CAPABILITY_CLASSES: dict[str, str] = {
    "chat": "traittutor.agents.chat.capability:ChatCapability",
    "deep_solve": "traittutor.capabilities.solve.capability:DeepSolveCapability",
    "learning_exploration": ("traittutor.capabilities.chat_modes:LearningExplorationCapability"),
    "knowledge_diagram": "traittutor.capabilities.chat_modes:KnowledgeDiagramCapability",
    "humanizer": "traittutor.capabilities.chat_modes:HumanizerCapability",
    "deep_research": "traittutor.agents.research.capability:DeepResearchCapability",
    "mastery_path": "traittutor.capabilities.mastery.capability:MasteryPathCapability",
}
