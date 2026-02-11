import asyncio

from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, ResultMessage, query


async def main(
    manager_name: str,
    project_directory: str,
    project_planning_directory: str,
):
    # Agentic loop: streams messages as Claude works
    async for message in query(
        prompt=f"""
Start by converting the project description in {project_planning_directory}/PROJECT.md
into a list of tasks. Each task should have a separate file name under the 
`{project_planning_directory}/tasks/` directory. The content of the file should follow
the template in `{project_planning_directory}/tasks/TEMPLATE.md`.

Once you have created the tasks, you should start assigning them to the Programmer
agents. You do this by checking the `{project_planning_directory}/team/`, which contains
one file per programmer. If the file states that the agent is available, you can assign
the task to it.

Once you have assigned all tasks, or you cannot assign any more tasks, e.g. no more
programmers available, you should enter a wait loop where you periodically check to see
if more programmers become available or if any tasks are completed/updated.

You should routinely say what you are doing, e.g. assigned task to X programmer,
waiting for team availability, etc. You should also routinely report the progress of
the project.

When the project is completed, you should exit.
""",
        options=ClaudeAgentOptions(
            allowed_tools=["Read", "Edit", "Glob"],  # Tools Claude can use
            permission_mode="acceptEdits",  # Auto-approve file edits
            system_prompt=f"""
You are {manager_name}, a manager of a team of AI agents. Your responsibility is to
manage the project planning and execution. 
        """,
        ),
    ):
        # Print human-readable output
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if hasattr(block, "text"):
                    print(block.text)  # Claude's reasoning
                elif hasattr(block, "name"):
                    print(f"Tool: {block.name}")  # Tool being called
        elif isinstance(message, ResultMessage):
            print(f"Done: {message.subtype}")  # Final result
        else:
            print(type(message))


asyncio.run(
    main(
        manager_name="Alice Chen",
        project_directory="castings/calculator",
        project_planning_directory="castings/calculator-planning",
    )
)
