def project_info_prompt(github_repo: str, epic: str, clone_path: str, status_path: str):
    # Build epic context
    if epic:
        epic_context = f"Top-level epic: #{epic}"
    else:
        epic_context = """
Top-level epic: The user didn't provide an epic; you will need to discuss with the user
what they want to work on and create the epic accordingly.
""".strip()

    return f"""
GitHub repo: {github_repo}
Local clone: {clone_path}
Status file: {status_path}
{epic_context}
""".strip()


def orchestrator_mcp_tools_prompt():
    return """
## Orchestrator MCP Tool

All agents share the SAME GitHub account. As such, direct GitHub activity from other
agents can appear as "yours." To work around this, any mutating activity on the GitHub
repo should be done via the Orchestrator MCP tool. The Orchestrator tool will ensure
that all activities are signed by the agent making the call to the MCP tool.
""".strip()
