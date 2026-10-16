import click


@click.command("web")
@click.option("--host", default="127.0.0.1", show_default=True, help="Bind host")
@click.option("--port", default=8000, show_default=True, help="Bind port")
@click.option("--reload", is_flag=True, help="Enable auto-reload (development)")
def web_cmd(host, port, reload):
    """Start the web interface."""
    import uvicorn

    uvicorn.run("greek.web.app:app", host=host, port=port, reload=reload)
