"""Resolve host-local macOS window identities through the SDK's own discovery."""

from computer_use.diagnostics import local_failure


async def app_for_window(computer, *, process_id: int, window_id: int):
    """Return the App instance for one observed macOS root window.

    IDs must come from the same host as the native driver. Discovery requires
    the driver's application and window nativeIdentity extensions; missing or
    ambiguous matches fail. Only the owning process's windows are queried.
    The returned App retains the normal scope, including confirmed attached
    dialogs, and normal native lifetime validation. IDs are not authorization.
    """
    if any(type(value) is not int or value <= 0 for value in (process_id, window_id)):
        raise ValueError("process_id and window_id must be positive integers")
    controller = computer._controller
    directory = controller.reader.directory
    catalogue = await directory.applications()
    applications = [
        item
        for item in catalogue["applications"]
        if item["isRunning"] and item.get("nativeIdentity") == {"processId": process_id}
    ]
    if catalogue["platform"] != "macos" or len(applications) != 1:
        raise local_failure(
            "native process did not resolve to exactly one observed macOS application",
            "rebuild the local driver and confirm a live independent window before retrying",
            "stale_target",
        )
    application = applications[0]
    surfaces = await directory.surfaces(application["id"])
    # Publish and resolve the same retained choices that Computer.app() consumes.
    choices = controller.interfaces.discovery.publish_choices(application["displayName"], surfaces)
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
