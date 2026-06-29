# Creating a New Skill

Skill creation involves these steps:

1. Understand the skill with concrete examples
2. Check for existing skills (run /skill-duplicate-check)
3. Plan reusable skill contents (scripts, references, assets)
4. Initialize the skill (run init_skill.py)
5. Edit the skill (implement resources and write SKILL.md)
6. Package the skill (run package_skill.py)
7. Iterate based on real usage

Follow these steps in order, skipping only if there is a clear reason why they are not applicable.

## Step 1: Understanding the Skill with Concrete Examples

Skip this step only when the skill's usage patterns are already clearly understood. It remains valuable even when working with an existing skill.

For example, when building an image-editor skill, relevant questions include:

- "What functionality should the image-editor skill support? Editing, rotating, anything else?"
- "Can you give some examples of how this skill would be used?"
- "I can imagine users asking for things like 'Remove the red-eye from this image' or 'Rotate this image'. Are there other ways you imagine this skill being used?"
- "What would a user say that should trigger this skill?"

## Step 2: Check for Existing Skills

Before creating a new skill, check whether a similar skill already exists. Run `/skill-duplicate-check` with a short description of the skill's purpose (e.g., `/skill-duplicate-check buck build helper`).

This searches across all three skill sources — Marketplace, Component Library, and `.llms/skills` — and presents matching results. If a close match is found, consider:

- **Using the existing skill** instead of creating a new one
- **Extending an existing skill** with the missing functionality
- **Proceeding with creation** if no existing skill covers the need

Skip this step only if you have already confirmed no duplicate exists.

## Step 3: Planning the Reusable Skill Contents

To turn concrete examples into an effective skill, analyze each example by:

1. Considering how to execute on the example from scratch
2. Identifying what scripts, references, and assets would be helpful when executing these workflows repeatedly

Examples:

- `pdf-editor` skill for "Help me rotate this PDF" → rotating a PDF requires re-writing the same code each time → store `scripts/rotate_pdf.py`
- `frontend-webapp-builder` skill for "Build me a todo app" → writing a frontend webapp requires the same boilerplate each time → store `assets/hello-world/` template
- `big-query` skill for "How many users have logged in today?" → querying BigQuery requires re-discovering table schemas each time → store `references/schema.md`

## Step 4: Initializing the Skill

Skip this step only if the skill being developed already exists, and iteration or packaging is needed. In this case, continue to the next step.

When creating a new skill from scratch, run `init_skill.py` to generate the directory structure with a SKILL.md template and example `scripts/`, `references/`, and `assets/` directories:

```bash
scripts/init_skill.py <skill-name> --path <output-directory>
```

After initialization, customize or remove the generated files as needed.

## Step 5: Edit the Skill

See the editing guidance in SKILL.md (Start with Reusable Skill Contents, Update SKILL.md sections).

## Step 6: Packaging a Skill

Once development of the skill is complete, it must be packaged into a distributable .skill file that gets shared with the user:

```bash
scripts/package_skill.py <path/to/skill-folder> [output-directory]
```

The packaging script validates (frontmatter, naming, description, file organization) and then creates a `.skill` zip file. Fix any reported validation errors and re-run.

## Step 7: Iterate

After testing the skill, users may request improvements.

**Iteration workflow:**

1. Use the skill on real tasks
2. Notice struggles or inefficiencies
3. Identify how SKILL.md or bundled resources should be updated
4. Implement changes
5. Re-run the Quality Checklist and verify packaging succeeds

## Creating a Claude Templates Skill

If creating a skill inside fbcode/claude-templates/components, also refer to fbcode/claude-templates/components/skills/CLAUDE.md for Meta-specific conventions and requirements.
