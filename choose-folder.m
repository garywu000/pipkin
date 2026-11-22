#import <AppKit/AppKit.h>

int main(void) {
    @autoreleasepool {
        NSApplication *app = [NSApplication sharedApplication];
        [app setActivationPolicy:NSApplicationActivationPolicyRegular];
        [app activateIgnoringOtherApps:YES];

        NSOpenPanel *panel = [NSOpenPanel openPanel];
        panel.title = @"Choose a workspace for Pipkin";
        panel.message = @"Pipkin will inspect text files in this folder.";
        panel.prompt = @"Choose folder";
        panel.canChooseDirectories = YES;
        panel.canChooseFiles = NO;
        panel.allowsMultipleSelection = NO;
        if ([panel runModal] != NSModalResponseOK) return 3;

        const char *path = panel.URL.fileSystemRepresentation;
        if (path == NULL) return 1;
        return puts(path) < 0 ? 1 : 0;
    }
}
