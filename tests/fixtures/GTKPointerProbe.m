#import <AppKit/AppKit.h>
#include <dlfcn.h>
#include <stdio.h>
#include <unistd.h>

// Opt-in GTK regression fixture: load the explicitly configured library without modifying it.
static FILE *output;
static void *gtk;
static void (*widget_show_all)(void *);
static void (*container_add)(void *, void *);
static void *(*button_new)(const char *);
static void *(*window_new)(int);
static void (*window_title)(void *, const char *);
static void (*window_size)(void *, int, int);
static void (*window_move)(void *, int, int);
static unsigned long (*signal_connect)(void *, const char *, void *, void *, void *, int);
static void *(*widget_window)(void *);
static NSWindow *(*native_window)(void *);
static void (*main_quit)(void);
static void log_click(void *widget, void *name) {
    fprintf(output, "{\"kind\":\"clicked\",\"target\":\"%s\"}\n", (char *)name);
    fflush(output);
}
static void native_filter_log(NSEvent *event) {
    if (event.type == NSEventTypeLeftMouseDown || event.type == NSEventTypeLeftMouseUp ||
        event.type == NSEventTypeMouseMoved || event.type == NSEventTypeMouseEntered ||
        event.type == NSEventTypeMouseExited) {
        fprintf(output, "{\"kind\":\"native\",\"type\":%lu,\"window\":%ld,\"x\":%.2f,\"y\":%.2f}\n",
                (unsigned long)event.type, (long)event.windowNumber, event.locationInWindow.x,
                event.locationInWindow.y);
        fflush(output);
    }
}
static int event_filter(void *event, void *unused, void *data) {
    native_filter_log((__bridge NSEvent *)event);
    return 0;
}
static int gdk_event(void *widget, void *event, void *name) {
    int type = *(int *)event;
    if (type == 4 || type == 7 || type == 3 || type == 10 || type == 11) {
        fprintf(output, "{\"kind\":\"gdk\",\"type\":%d,\"target\":\"%s\"}\n", type, (char *)name);
        fflush(output);
    }
    return 0;
}
static void make_window(const char *title, int x, int y) {
    void *w = window_new(0);
    window_title(w, title);
    window_size(w, 360, 220);
    window_move(w, x, y);
    void *button = button_new(title);
    container_add(w, button);
    signal_connect(button, "clicked", log_click, (void *)title, NULL, 0);
    signal_connect(w, "event", gdk_event, (void *)title, NULL, 0);
    widget_show_all(w);
    NSWindow *ns = native_window(widget_window(w));
    [ns orderBack:nil];
    fprintf(output, "{\"kind\":\"window\",\"title\":\"%s\",\"id\":%ld}\n", title, (long)ns.windowNumber);
    fflush(output);
}
int main(int argc, char **argv) {
    @autoreleasepool {
        if (argc < 3)
            return 2;
        output = fopen(argv[2], "w");
        if (!output)
            return 3;
        fprintf(output, "{\"kind\":\"started\",\"pid\":%d}\n", getpid());
        fflush(output);
        gtk = dlopen(argv[1], RTLD_NOW | RTLD_GLOBAL);
        if (!gtk) {
            fprintf(stderr, "%s\n", dlerror());
            return 4;
        }
#define LOAD(variable, name)                                                                                           \
    variable = dlsym(gtk, name);                                                                                       \
    if (!variable)                                                                                                     \
        return 5;
        LOAD(widget_show_all, "gtk_widget_show_all");
        LOAD(container_add, "gtk_container_add");
        LOAD(button_new, "gtk_button_new_with_label");
        LOAD(window_new, "gtk_window_new");
        LOAD(window_title, "gtk_window_set_title");
        LOAD(window_size, "gtk_window_set_default_size");
        LOAD(window_move, "gtk_window_move");
        LOAD(signal_connect, "g_signal_connect_data");
        LOAD(widget_window, "gtk_widget_get_window");
        LOAD(native_window, "gdk_quartz_window_get_nswindow");
        LOAD(main_quit, "gtk_main_quit");
        int (*init)(int *, char ***) = dlsym(gtk, "gtk_init_check");
        int n = 1;
        char **a = argv;
        if (!init(&n, &a))
            return 6;
        void (*add_filter)(void *, void *, void *) = dlsym(gtk, "gdk_window_add_filter");
        add_filter(NULL, event_filter, NULL);
        // Overlap the windows: an incorrect cached pointer window can steal the other click.
        NSPoint pointer = [NSEvent mouseLocation];
        NSRect screen = NSScreen.screens.firstObject.frame;
        int x = MAX(20, MIN(screen.size.width - 420, pointer.x - 180));
        int y = MAX(80, MIN(screen.size.height - 290, screen.size.height - pointer.y - 110));
        make_window("GTK Alpha", x, y);
        make_window("GTK Beta", x + 40, y + 30);
        [NSApp setActivationPolicy:NSApplicationActivationPolicyRegular];
        [NSApp finishLaunching];
        [NSTimer scheduledTimerWithTimeInterval:45
                                        repeats:NO
                                          block:^(NSTimer *t) {
                                            main_quit();
                                          }];
        void (*loop)(void) = dlsym(gtk, "gtk_main");
        loop();
        fclose(output);
    }
}
