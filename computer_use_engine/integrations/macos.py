"""Resolve host-local macOS window identities through the SDK's own discovery."""

from computer_use.diagnostics import local_failure


async def app_for_window(computer, *, process_id: int, window_id: int):
    """Return the App instance for one observed macOS root window.

    IDs must come from the same host as the native driver. Discovery requires
    the driver's nativeIdentity extension; missing or ambiguous matches fail.
    The returned App retains the normal scope, including confirmed attached
    dialogs, and normal native lifetime validation. IDs are not authorization.
    """
    if any(type(value) is not int or value <= 0 for value in (process_id, window_id)):
        raise ValueError("process_id and window_id must be positive integers")
    choices = await computer.list_apps()
    # Resolve the same retained choices that Computer.app() consumes.
    controller = computer._controller
    matches = []
    identity = dict(processId=process_id, windowId=window_id)
    for choice in choices:
        entry = controller.interfaces.references.resolve(choice["id"], controller.transport.session)
        if entry.directory is None or entry.directory["platform"] != "macos" or entry.window is None:
            continue
        if any(
            item["window"] == entry.window and item.get("nativeIdentity") == identity
            for item in entry.directory["surfaces"]
        ):
            matches.append(choice["id"])
    if len(matches) != 1:
        raise local_failure(
            "native window did not resolve to exactly one observed macOS App instance",
            "rebuild the local driver and confirm a live independent window before retrying",
            "stale_target",
        )
    return computer.app(matches[0])
