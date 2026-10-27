#include <ApplicationServices/ApplicationServices.h>
#include <stdlib.h>
#include <string.h>

// Inject only into the disposable SDK driver. The fixture/provider and the
// ordinary value getter remain untouched, so alternate-API recovery is real.
static int mode(const char *name) {
    const char *value = getenv("AX_READ_FAULT");
    return value && !strcmp(value, name);
}
static int target(AXUIElementRef element, CFStringRef identifier) {
    CFTypeRef value = NULL;
    AXError error = AXUIElementCopyAttributeValue(element, CFSTR("AXIdentifier"), &value);
    int matches = error == kAXErrorSuccess && value && CFEqual(value, identifier);
    if (value)
        CFRelease(value);
    return matches;
}
static int pending_count;
static AXError read_batch(AXUIElementRef element, CFArrayRef fields, AXCopyMultipleAttributeOptions options,
                          CFArrayRef *values) {
    if (mode("batch") && target(element, CFSTR("ax-read-button"))) {
        *values = NULL;
        return kAXErrorNotImplemented;
    }
    return AXUIElementCopyMultipleAttributeValues(element, fields, options, values);
}
static AXError read_names(AXUIElementRef element, CFArrayRef *names) {
    if (mode("catalogue") && target(element, CFSTR("ax-read-button"))) {
        *names = NULL;
        return kAXErrorCannotComplete;
    }
    return AXUIElementCopyAttributeNames(element, names);
}
static AXError read_count(AXUIElementRef element, CFStringRef field, CFIndex *count) {
    if (CFEqual(field, CFSTR("AXChildren")) && target(element, CFSTR("ax-read-canvas"))) {
        if (mode("count")) {
            *count = 0;
            return kAXErrorNotImplemented;
        }
        if (mode("post-count") && pending_count) {
            pending_count = 0;
            *count = 0;
            return kAXErrorCannotComplete;
        }
    }
    return AXUIElementGetAttributeValueCount(element, field, count);
}
static AXError read_range(AXUIElementRef element, CFStringRef field, CFIndex index, CFIndex length,
                          CFArrayRef *values) {
    int selected = CFEqual(field, CFSTR("AXChildren")) && target(element, CFSTR("ax-read-canvas"));
    if (selected && mode("range")) {
        *values = NULL;
        return kAXErrorNotImplemented;
    }
    AXError error = AXUIElementCopyAttributeValues(element, field, index, length, values);
    if (selected && mode("post-count") && error == kAXErrorSuccess)
        pending_count = 1;
    return error;
}
__attribute__((used)) static struct {
    const void *replacement;
    const void *replacee;
} interpositions[] __attribute__((section("__DATA,__interpose"))) = {
    {(const void *)read_batch, (const void *)AXUIElementCopyMultipleAttributeValues},
    {(const void *)read_names, (const void *)AXUIElementCopyAttributeNames},
    {(const void *)read_count, (const void *)AXUIElementGetAttributeValueCount},
    {(const void *)read_range, (const void *)AXUIElementCopyAttributeValues}};
