import asyncio

from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, ResultMessage, query


async def main(
    programmer_name: str,
    project_directory: str,
    project_planning_directory: str,
):
    # Agentic loop: streams messages as Claude works
    async for message in query(
        prompt=f"""
Start by declaring yourself available to pick a task by creating a file under the
`{project_planning_directory}/team/` directory with your name as the filename. Use the
template in `{project_planning_directory}/team/TEMPLATE.md` as a guide.

Then wait until you are assigned a task by the manager. The manager will do this by
updating the same file in the `{project_planning_directory}/team` directory with your
assignment.

Once you are assigned a task, you should work on it and then continuously update the
task file in the `{project_directory}/tasks/` directory with your progress; put your
comments under the Comments section. When you are done with the task, update the status
of the task in the `{project_directory}/tasks/` file to COMPLETED and add the necessary
final comments to the Comments section.`
""",
        options=ClaudeAgentOptions(
            allowed_tools=["Read", "Edit", "Glob"],  # Tools Claude can use
            permission_mode="acceptEdits",  # Auto-approve file edits
            system_prompt=f"""
You are a programmer named {programmer_name}. You work with a group of AI agents to
implement a software project end-to-end. 
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


asyncio.run(
    main(
        programmer_name="John Doe",
        project_directory="castings/calculator",
        project_planning_directory="castings/calculator-planning",
    )
)
