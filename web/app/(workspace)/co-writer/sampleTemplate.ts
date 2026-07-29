const FENCE = "```";

export const CO_WRITER_SAMPLE_TEMPLATE = `# TraitTutor Co-Writer

> TraitTutor's built-in writing canvas for notes, reports, tutorials, and AI-assisted drafts.

### Features

- Support Standard Markdown / CommonMark / GFM for everyday writing
- Real-time preview for headings, tables, code, math, flowchart, and sequence diagrams
- AI editing workflows for rewrite, shorten, and expand
- HTML tag decoding for tags like <sub>, <sup>, <abbr>, and <mark>
- A practical starter draft for TraitTutor product docs and learning content

## Headers (Underline)

TraitTutor Learning Note
=============

TraitTutor Study Outline
-------------

### Characters

----

~~Deprecated behavior~~ <s>Legacy formatting path</s>
*Italic* _Italic_
**Emphasis** __Emphasis__
***Emphasis Italic*** ___Emphasis Italic___

Superscript: X<sup>2</sup>, Subscript: O<sub>2</sub>

**Abbreviation(link HTML abbr tag)**

The <abbr title="Large Language Model">LLM</abbr> layer powers TraitTutor while the <abbr title="Retrieval Augmented Generation">RAG</abbr> layer provides grounded knowledge support.

### Blockquotes

> TraitTutor helps students turn questions into structured understanding.
>
> "Learn deeply, write clearly.", [TraitTutor](#traittutor-co-writer)

### Links

[TraitTutor Co-Writer](#traittutor-co-writer "co-writer section")

[TraitTutor Learning Note](#traittutor-learning-note)

[TraitTutor Website](https://traittutor.info)

[Reference link][traittutor-doc]

[traittutor-doc]: #traittutor-learning-note

### Code Blocks

#### Inline code

\`traittutor chat --once "Summarize this section"\`

#### Code Blocks (Indented style)

    from traittutor.runtime.orchestrator import ChatOrchestrator
    orchestrator = ChatOrchestrator()
    print("TraitTutor is ready.")

#### Python

${FENCE}python
from traittutor.runtime.orchestrator import ChatOrchestrator
from traittutor.core.context import UnifiedContext


async def run_demo() -> str:
    orchestrator = ChatOrchestrator()
    context = UnifiedContext(
        user_query="Explain Newton's second law",
        capability="chat",
    )
    result = await orchestrator.run(context)
    return result.get("response", "")
${FENCE}

#### JSON config

${FENCE}json
{
  "app_name": "TraitTutor",
  "default_capability": "chat",
  "enabled_tools": ["rag", "web_search", "code_execution", "reason"],
  "ui": {
    "co_writer_template": true
  }
}
${FENCE}

#### HTML code

${FENCE}html
<section class="traittutor-card">
  <h1>TraitTutor</h1>
  <p>Write, revise, and organize learning content with AI.</p>
</section>
${FENCE}

### Images

![](/logo-ver2.png)

> TraitTutor brand mark used inside the co-writer template.

### Lists

- TraitTutor Chat
- TraitTutor Co-Writer
- TraitTutor Research

1. Draft a concept note
2. Ask AI to refine it
3. Export the polished markdown

### Tables

Feature       | Description
------------- | -------------
Co-Writer     | Draft and refine Markdown content
Chat          | Ask questions and iterate ideas
Research      | Build structured multi-step reports

| Capability    | Primary Use Case                     |
| ------------- | ------------------------------------ |
| \`chat\`       | General tutoring and guidance        |
| \`deep_solve\` | Structured problem solving           |
| \`deep_question\` | Question generation and validation |

### Markdown extras

- [x] Draft a TraitTutor product note
- [x] Add references and structure
- [ ] Polish the final explanation
  - [ ] Check headings
  - [ ] Check citations

### TeX (LaTeX)

$$ E=mc^2 $$

Inline $$E=mc^2$$ appears in physics notes, and Inline $$a^2+b^2=c^2$$ appears in geometry notes.

$$\\sqrt{3x-1}+(1+x)^2$$

$$ \\sin(\\alpha)^{\\theta}=\\sum_{i=0}^{n}(x^i + \\cos(f))$$

### FlowChart

${FENCE}flow
st=>start: Student asks a question
op=>operation: TraitTutor analyzes intent
cond=>condition: Need deep workflow?
chat=>operation: Answer with chat capability
solve=>operation: Route to deep solve
e=>end: Return structured response

st->op->cond
cond(no)->chat
cond(yes)->solve
chat->e
solve->e
${FENCE}

### Sequence Diagram

${FENCE}seq
Student->TraitTutor: Ask for help
TraitTutor->KnowledgeBase: Load context
Note right of TraitTutor: Collect memory\\nand relevant knowledge
TraitTutor-->Student: Return guided response
Student->>TraitTutor: Request rewrite in co-writer
${FENCE}

### End
`;
