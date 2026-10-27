// Test-only AX return fault; the original call runs exactly once.
#include <ApplicationServices/ApplicationServices.h>
#include <dlfcn.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
typedef AXError (*Action)(AXUIElementRef, CFStringRef);
static AXError fault_action(AXUIElementRef element, CFStringRef action) {
    pid_t pid = 0;
    const char *expected = getenv("COMPUTER_USE_TEST_AX_ERROR_PID");
    CFTypeRef role = NULL;
    const char *window = getenv("COMPUTER_USE_TEST_AX_ERROR_WINDOW");
    int match = expected && window && atoi(expected) > 0 && AXUIElementGetPid(element, &pid) == 0 &&
                pid == atoi(expected) && CFEqual(action, CFSTR("AXPress")) &&
                AXUIElementCopyAttributeValue(element, CFSTR("AXRole"), &role) == 0 && role &&
                (CFEqual(role, CFSTR("AXCheckBox")) || CFEqual(role, CFSTR("AXRadioButton")));
    if (match && window) {
        typedef AXError (*Window)(AXUIElementRef, CGWindowID *);
        Window get_window = (Window)dlsym(RTLD_DEFAULT, "_AXUIElementGetWindow");
        CGWindowID actual = 0;
        match = get_window && get_window(element, &actual) == 0 && actual == strtoul(window, NULL, 10);
    }
    if (role)
        CFRelease(role);
    AXError status = AXUIElementPerformAction(element, action);
    if (match && status == kAXErrorSuccess) {
        const char *path = getenv("COMPUTER_USE_TEST_AX_ERROR_LOG");
        if (path) {
            FILE *file = fopen(path, "a");
            if (file) {
                fprintf(file, "{\"caller\":%d,\"target\":%d,\"originalStatus\":%d,\"returnedStatus\":%d}\n", getpid(),
                        pid, status, kAXErrorActionUnsupported);
                fclose(file);
            }
        }
        return kAXErrorActionUnsupported;
    }
    return status;
}
__attribute__((used)) static struct {
    const void *replacement;
    const void *original;
} interpose[] __attribute__((section("__DATA,__interpose"))) = {
    {(const void *)fault_action, (const void *)AXUIElementPerformAction}};
