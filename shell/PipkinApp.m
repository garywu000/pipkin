// Pipkin.app native shell.
//
// Runs Pipkin's local Python server (which loads the model on demand) and shows the
// interface in a native WebKit window. Quitting unloads the model and stops the server.
// Build with shell/build.sh.

#import <Cocoa/Cocoa.h>
#import <QuartzCore/QuartzCore.h>
#import <WebKit/WebKit.h>
#import <UserNotifications/UserNotifications.h>
#import "PipkinPet.h"
#import "PipkinBrowser.h"

static NSString *const kAppId = @"local.browser-model";
static const NSInteger kMainPort = 8765;
static const NSInteger kDevPort = 8766;

static NSString *LogDir(void) {
    NSString *dir = [NSHomeDirectory() stringByAppendingPathComponent:@"Library/Logs/Pipkin"];
    [[NSFileManager defaultManager] createDirectoryAtPath:dir withIntermediateDirectories:YES attributes:nil error:nil];
    return dir;
}

static void AppLog(NSString *format, ...) {
    va_list args;
    va_start(args, format);
    NSString *line = [[NSString alloc] initWithFormat:format arguments:args];
    va_end(args);
    NSString *stamped = [NSString stringWithFormat:@"%@ %@\n", [NSDate date], line];
    NSString *path = [LogDir() stringByAppendingPathComponent:@"app.log"];
    NSFileHandle *handle = [NSFileHandle fileHandleForWritingAtPath:path];
    if (!handle) {
        [stamped writeToFile:path atomically:NO encoding:NSUTF8StringEncoding error:nil];
        return;
    }
    [handle seekToEndOfFile];
    [handle writeData:[stamped dataUsingEncoding:NSUTF8StringEncoding]];
    [handle closeFile];
}

/// Calls back on the main queue with YES when a Pipkin server answers on the port.
static void CheckHealth(NSInteger port, void (^done)(BOOL ok)) {
    NSURL *url = [NSURL URLWithString:[NSString stringWithFormat:@"http://127.0.0.1:%ld/health", (long)port]];
    NSMutableURLRequest *request = [NSMutableURLRequest requestWithURL:url
                                                           cachePolicy:NSURLRequestReloadIgnoringLocalCacheData
                                                       timeoutInterval:0.8];
    [[[NSURLSession sharedSession] dataTaskWithRequest:request completionHandler:^(NSData *data, NSURLResponse *response, NSError *error) {
        BOOL ok = NO;
        if (data && !error && [(NSHTTPURLResponse *)response statusCode] == 200) {
            id json = [NSJSONSerialization JSONObjectWithData:data options:0 error:nil];
            ok = [json isKindOfClass:[NSDictionary class]] && [json[@"app"] isEqual:kAppId];
        }
        dispatch_async(dispatch_get_main_queue(), ^{ done(ok); });
    }] resume];
}

static void WaitForHealth(NSInteger port, NSDate *deadline, void (^done)(BOOL ok)) {
    CheckHealth(port, ^(BOOL ok) {
        if (ok || [deadline timeIntervalSinceNow] < 0) {
            done(ok);
            return;
        }
        dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(0.25 * NSEC_PER_SEC)), dispatch_get_main_queue(), ^{
            WaitForHealth(port, deadline, done);
        });
    });
}

static NSColor *ParseCSSColor(NSString *css) {
    NSScanner *scanner = [NSScanner scannerWithString:css];
    [scanner scanUpToCharactersFromSet:[NSCharacterSet decimalDigitCharacterSet] intoString:nil];
    double r = 0, g = 0, b = 0;
    if (![scanner scanDouble:&r]) return nil;
    [scanner scanUpToCharactersFromSet:[NSCharacterSet decimalDigitCharacterSet] intoString:nil];
    if (![scanner scanDouble:&g]) return nil;
    [scanner scanUpToCharactersFromSet:[NSCharacterSet decimalDigitCharacterSet] intoString:nil];
    if (![scanner scanDouble:&b]) return nil;
    return [NSColor colorWithSRGBRed:r / 255.0 green:g / 255.0 blue:b / 255.0 alpha:1];
}

static NSColor *DefaultBackground(void) {
    return [NSColor colorWithName:nil dynamicProvider:^NSColor *(NSAppearance *appearance) {
        BOOL dark = [[appearance bestMatchFromAppearancesWithNames:@[NSAppearanceNameAqua, NSAppearanceNameDarkAqua]] isEqual:NSAppearanceNameDarkAqua];
        return dark ? [NSColor colorWithSRGBRed:0x10 / 255.0 green:0x18 / 255.0 blue:0x15 / 255.0 alpha:1]
                    : [NSColor colorWithSRGBRed:0xe1 / 255.0 green:0xe6 / 255.0 blue:0xdf / 255.0 alpha:1];
    }];
}

static NSString *StatusPage(NSString *title, NSString *detail, BOOL spinner) {
    NSString *spin = spinner ? @"<div class=orb><div class=s></div></div>" : @"";
    return [NSString stringWithFormat:
        @"<!doctype html><meta charset=utf-8><style>"
        @":root{color-scheme:light dark;font:15px -apple-system,sans-serif}"
        @"body{margin:0;height:100vh;display:grid;place-items:center;background:#edf3f1;background-image:radial-gradient(60%% 50%% at 50%% 0%%,rgb(16 172 118/.2),rgb(14 165 233/.08) 52%%,transparent 80%%);color:#36453f;text-align:center}"
        @"@media(prefers-color-scheme:dark){body{background:#0c0f0e;background-image:radial-gradient(60%% 50%% at 50%% 0%%,rgb(62 213 152/.12),rgb(56 217 217/.05) 52%%,transparent 80%%);color:#c2cbc6}.orb{background:rgb(20 25 24/.8);border-color:#222b28}.s{border-color:#3ed59833;border-top-color:#3ed598}}"
        @".wrap{animation:up .48s cubic-bezier(.16,1,.3,1) both}"
        @".orb{width:54px;height:54px;margin:0 auto 14px;border-radius:16px;display:grid;place-items:center;background:rgb(255 255 255/.85);border:1px solid #cddbd5;box-shadow:0 12px 32px rgb(10 42 30/.12)}"
        @"h1{font-size:18px;font-weight:650;letter-spacing:-.025em;margin:0 0 6px}p{margin:0;max-width:420px;line-height:1.5;opacity:.78}"
        @".s{width:22px;height:22px;border-radius:50%%;border:2.5px solid #0d8a5e33;border-top-color:#0d8a5e;animation:r .72s linear infinite}"
        @"@keyframes r{to{transform:rotate(360deg)}}@keyframes up{from{opacity:0;transform:translateY(10px) scale(.97)}}"
        @"</style><div class=wrap>%@<h1>%@</h1><p>%@</p></div>", spin, title, detail];
}

#pragma mark - Window

@class AppDelegate;

@interface PipkinWindow : NSObject <WKNavigationDelegate, WKUIDelegate, WKScriptMessageHandler, WKDownloadDelegate, NSWindowDelegate>
@property (strong) NSWindow *window;
@property (strong) WKWebView *webView;
@property (assign) NSInteger port;
@property (assign) BOOL isMain;
@property (assign) BOOL busy;
@property (weak) AppDelegate *owner;
@property (strong) NSSplitView *split;
@property (strong) PipkinBrowserPane *browser;
- (void)toggleBrowser;
- (void)openInBrowser:(NSURL *)url;
- (instancetype)initWithPort:(NSInteger)port title:(NSString *)title autosave:(NSString *)autosave main:(BOOL)isMain owner:(AppDelegate *)owner;
- (void)showStatus:(NSString *)title detail:(NSString *)detail spinner:(BOOL)spinner;
- (void)loadApp;
- (void)runJS:(NSString *)script;
@end

@interface AppDelegate : NSObject <NSApplicationDelegate, UNUserNotificationCenterDelegate>
@property (strong) PipkinWindow *main;
@property (strong) PipkinWindow *dev;
@property (strong) NSTask *server;
@property (strong) NSTask *devServer;
@property (strong) id busyActivity;
@property (assign) BOOL quitConfirmed;
@property (copy) NSString *devDir;
@property (strong) PipkinPet *pet;
@property (assign) BOOL mainParked;
@property (assign) BOOL parkingMain;
@property (assign) BOOL mainReady;
@property (assign) NSUInteger launchGeneration;
@property (copy) NSString *pendingMainAction;
@property (copy) NSString *pendingAskText;
- (void)pageMessage:(NSDictionary *)message from:(PipkinWindow *)window;
- (void)windowClosed:(PipkinWindow *)window;
- (void)closeMainWindow;
@end

@implementation PipkinWindow

- (instancetype)initWithPort:(NSInteger)port title:(NSString *)title autosave:(NSString *)autosave main:(BOOL)isMain owner:(AppDelegate *)owner {
    self = [super init];
    if (!self) return nil;
    _port = port;
    _isMain = isMain;
    _owner = owner;

    WKWebViewConfiguration *config = [WKWebViewConfiguration new];
    WKUserContentController *content = [WKUserContentController new];
    [content addScriptMessageHandler:self name:@"pipkin"];
    config.userContentController = content;
    config.websiteDataStore = [WKWebsiteDataStore defaultDataStore];
    if (@available(macOS 14.0, *)) {
        // Keep the page's timers and frames running while Pipkin is in the background, so a long Work run keeps
        // showing its progress and approvals instead of freezing until the window is focused.
        config.preferences.inactiveSchedulingPolicy = WKInactiveSchedulingPolicyNone;
    }
    config.mediaTypesRequiringUserActionForPlayback = WKAudiovisualMediaTypeNone;
    @try { [config.preferences setValue:@YES forKey:@"developerExtrasEnabled"]; } @catch (NSException *e) {}

    _webView = [[WKWebView alloc] initWithFrame:NSMakeRect(0, 0, 1240, 820) configuration:config];
    _webView.navigationDelegate = self;
    _webView.UIDelegate = self;
    _webView.allowsMagnification = NO;
    if (@available(macOS 13.3, *)) _webView.inspectable = YES;
    @try { [_webView setValue:@NO forKey:@"drawsBackground"]; } @catch (NSException *e) {}
    _webView.pageZoom = MAX(0.6, MIN(2.0, [[NSUserDefaults standardUserDefaults] doubleForKey:@"PageZoom"] ?: 1.0));

    NSWindowStyleMask style = NSWindowStyleMaskTitled | NSWindowStyleMaskClosable | NSWindowStyleMaskMiniaturizable | NSWindowStyleMaskResizable;
    _window = [[NSWindow alloc] initWithContentRect:NSMakeRect(0, 0, 1240, 820) styleMask:style backing:NSBackingStoreBuffered defer:NO];
    _window.title = title;
    _window.titleVisibility = NSWindowTitleHidden;
    _window.titlebarAppearsTransparent = YES;
    _window.backgroundColor = DefaultBackground();
    _window.minSize = NSMakeSize(720, 520);
    _window.tabbingMode = NSWindowTabbingModeDisallowed;
    _window.releasedWhenClosed = NO;
    _window.delegate = self;
    _split = [[NSSplitView alloc] initWithFrame:NSMakeRect(0, 0, 1240, 820)];
    _split.vertical = YES;
    _split.dividerStyle = NSSplitViewDividerStyleThin;
    _split.autoresizingMask = NSViewWidthSizable | NSViewHeightSizable;
    [_split addSubview:_webView];
    _window.contentView = _split;
    if (![_window setFrameUsingName:autosave]) [_window center];
    _window.frameAutosaveName = autosave;
    return self;
}

#pragma mark Browser pane

- (BOOL)browserOpen {
    return self.browser && !self.browser.hidden;
}

- (void)showBrowser:(BOOL)show {
    if (show && !self.browser) {
        self.browser = [[PipkinBrowserPane alloc] initWithFrame:NSMakeRect(0, 0, 520, 820)];
        __weak PipkinWindow *weakSelf = self;
        self.browser.onClose = ^{ [weakSelf showBrowser:NO]; };
        [self.split addSubview:self.browser];
    }
    if (!self.browser || show == [self browserOpen]) {
        if (show) [self.browser loadHomeIfEmpty];
        return;
    }
    CGFloat total = self.split.bounds.size.width;
    self.browser.wantsLayer = YES;
    if (!show) {
        [[NSUserDefaults standardUserDefaults] setDouble:self.browser.frame.size.width forKey:@"BrowserPaneWidth"];
        __weak PipkinWindow *weakSelf = self;
        [NSAnimationContext runAnimationGroup:^(NSAnimationContext *ctx) {
            ctx.duration = 0.22;
            ctx.timingFunction = [CAMediaTimingFunction functionWithControlPoints:0.22f :1.0f :0.36f :1.0f];
            ctx.allowsImplicitAnimation = YES;
            weakSelf.browser.animator.alphaValue = 0.0;
            [weakSelf.split setPosition:total ofDividerAtIndex:0];
            [weakSelf.split layoutSubtreeIfNeeded];
        } completionHandler:^{
            if (!weakSelf) return;
            weakSelf.browser.hidden = YES;
            weakSelf.browser.alphaValue = 1.0;
            [weakSelf.split adjustSubviews];
            [weakSelf.window makeFirstResponder:weakSelf.webView];
        }];
    } else {
        // Widen a small window so the chat keeps room next to the browser.
        double saved = [[NSUserDefaults standardUserDefaults] doubleForKey:@"BrowserPaneWidth"];
        CGFloat width = MAX(360, MIN(saved ?: 520, total - 420));
        if (total - width < 420) {
            NSRect frame = self.window.frame;
            NSRect screen = self.window.screen.visibleFrame;
            CGFloat grow = MIN(420 + width - total, NSMaxX(screen) - NSMaxX(frame));
            if (grow > 0) {
                frame.size.width += grow;
                [self.window setFrame:frame display:YES animate:YES];
                total = self.split.bounds.size.width;
            }
            width = MAX(320, MIN(width, total - 420));
        }
        CGFloat target = total - width - self.split.dividerThickness;
        self.browser.alphaValue = 0.0;
        self.browser.hidden = NO;
        [self.split adjustSubviews];
        [self.split setPosition:total - 1 ofDividerAtIndex:0];
        __weak PipkinWindow *weakSelf = self;
        [NSAnimationContext runAnimationGroup:^(NSAnimationContext *ctx) {
            ctx.duration = 0.26;
            ctx.timingFunction = [CAMediaTimingFunction functionWithControlPoints:0.22f :1.0f :0.36f :1.0f];
            ctx.allowsImplicitAnimation = YES;
            weakSelf.browser.animator.alphaValue = 1.0;
            [weakSelf.split setPosition:target ofDividerAtIndex:0];
            [weakSelf.split layoutSubtreeIfNeeded];
        } completionHandler:nil];
        [self.browser loadHomeIfEmpty];
    }
    [self runJS:[NSString stringWithFormat:@"window.PipkinPanels && PipkinPanels.browserState(%@)", show ? @"true" : @"false"]];
}

/// The agent's browser tools: open a page in a new tab or read the current tab, then hand the
/// text back to the page, which forwards it to the server.
- (void)agentBrowserRequest:(NSDictionary *)body {
    NSString *requestId = body[@"id"];
    NSString *op = [body[@"op"] isEqual:@"open"] ? @"open" : @"read";
    NSURL *url = [body[@"url"] isKindOfClass:[NSString class]] ? [NSURL URLWithString:body[@"url"]] : nil;
    NSString *scheme = url.scheme.lowercaseString;
    if (url && !([scheme isEqualToString:@"http"] || [scheme isEqualToString:@"https"])) url = nil;
    __weak PipkinWindow *weakSelf = self;
    void (^reply)(NSDictionary *) = ^(NSDictionary *result) {
        NSMutableDictionary *payload = [result mutableCopy] ?: [NSMutableDictionary dictionary];
        payload[@"id"] = requestId;
        NSData *json = [NSJSONSerialization dataWithJSONObject:payload options:0 error:nil];
        NSString *text = json ? [[NSString alloc] initWithData:json encoding:NSUTF8StringEncoding] : @"{}";
        [weakSelf runJS:[NSString stringWithFormat:@"window.PipkinPanels && PipkinPanels.browserReply(%@)", text]];
    };
    if ([op isEqual:@"read"] && ![self browserOpen]) {
        reply(@{@"error": @"The browser pane is closed, so there is no page to read."});
        return;
    }
    [self showBrowser:YES];
    [self.browser agentRequest:op url:url completion:reply];
}

- (void)toggleBrowser {
    [self showBrowser:![self browserOpen]];
    if ([self browserOpen] && !self.browser.webView.URL) [self.browser focusAddressBar];
}

- (void)openInBrowser:(NSURL *)url {
    [self showBrowser:YES];
    [self.browser openURL:url];
}

/// Links clicked in Pipkin open in the browser pane, unless Command is held or the user turned it off.
- (BOOL)routeLink:(NSURL *)url modifiers:(NSEventModifierFlags)flags {
    NSString *scheme = url.scheme.lowercaseString;
    if (!([scheme isEqualToString:@"http"] || [scheme isEqualToString:@"https"])) return NO;
    BOOL useBrowser = ![[NSUserDefaults standardUserDefaults] boolForKey:@"LinksOpenExternally"];
    if (flags & NSEventModifierFlagCommand) useBrowser = !useBrowser;
    if (!useBrowser) return NO;
    [self openInBrowser:url];
    return YES;
}

- (NSString *)origin {
    return [NSString stringWithFormat:@"http://127.0.0.1:%ld", (long)self.port];
}

- (void)showStatus:(NSString *)title detail:(NSString *)detail spinner:(BOOL)spinner {
    [self.webView loadHTMLString:StatusPage(title, detail, spinner) baseURL:nil];
}

- (void)loadApp {
    NSURL *url = [NSURL URLWithString:[[self origin] stringByAppendingString:@"/"]];
    [self.webView loadRequest:[NSURLRequest requestWithURL:url cachePolicy:NSURLRequestReloadIgnoringLocalCacheData timeoutInterval:15]];
}

- (void)runJS:(NSString *)script {
    [self.webView evaluateJavaScript:script completionHandler:nil];
}

- (BOOL)isAppURL:(NSURL *)url {
    return [url.host isEqualToString:@"127.0.0.1"] && url.port.integerValue == self.port;
}

#pragma mark Navigation

- (void)webView:(WKWebView *)webView decidePolicyForNavigationAction:(WKNavigationAction *)action decisionHandler:(void (^)(WKNavigationActionPolicy))decisionHandler {
    if (action.shouldPerformDownload) {
        decisionHandler(WKNavigationActionPolicyDownload);
        return;
    }
    NSURL *url = action.request.URL;
    NSString *scheme = url.scheme.lowercaseString;
    if ([scheme isEqualToString:@"about"] || [scheme isEqualToString:@"blob"] || [scheme isEqualToString:@"data"] || [self isAppURL:url]) {
        decisionHandler(WKNavigationActionPolicyAllow);
        return;
    }
    if (action.navigationType == WKNavigationTypeLinkActivated && [self routeLink:url modifiers:action.modifierFlags]) {
        decisionHandler(WKNavigationActionPolicyCancel);
        return;
    }
    if ([@[@"http", @"https", @"mailto"] containsObject:scheme]) [[NSWorkspace sharedWorkspace] openURL:url];
    decisionHandler(WKNavigationActionPolicyCancel);
}

- (void)webView:(WKWebView *)webView decidePolicyForNavigationResponse:(WKNavigationResponse *)response decisionHandler:(void (^)(WKNavigationResponsePolicy))decisionHandler {
    decisionHandler(response.canShowMIMEType ? WKNavigationResponsePolicyAllow : WKNavigationResponsePolicyDownload);
}

- (void)webView:(WKWebView *)webView navigationAction:(WKNavigationAction *)action didBecomeDownload:(WKDownload *)download {
    download.delegate = self;
}

- (void)webView:(WKWebView *)webView navigationResponse:(WKNavigationResponse *)response didBecomeDownload:(WKDownload *)download {
    download.delegate = self;
}

- (void)download:(WKDownload *)download decideDestinationUsingResponse:(NSURLResponse *)response suggestedFilename:(NSString *)name completionHandler:(void (^)(NSURL *))completion {
    NSSavePanel *panel = [NSSavePanel savePanel];
    panel.nameFieldStringValue = name.length ? name : @"Pipkin export";
    panel.directoryURL = [[NSFileManager defaultManager] URLsForDirectory:NSDownloadsDirectory inDomains:NSUserDomainMask].firstObject;
    [panel beginSheetModalForWindow:self.window completionHandler:^(NSModalResponse result) {
        if (result != NSModalResponseOK || !panel.URL) {
            completion(nil);
            return;
        }
        [[NSFileManager defaultManager] removeItemAtURL:panel.URL error:nil];
        completion(panel.URL);
    }];
}

- (void)download:(WKDownload *)download didFailWithError:(NSError *)error resumeData:(NSData *)resumeData {
    AppLog(@"Download failed: %@", error.localizedDescription);
}

- (void)webView:(WKWebView *)webView didFailProvisionalNavigation:(WKNavigation *)navigation withError:(NSError *)error {
    if (error.code == NSURLErrorCancelled) return;
    AppLog(@"Load failed on port %ld: %@", (long)self.port, error.localizedDescription);
    [self showStatus:@"Pipkin could not load" detail:@"The local server did not answer. Use View › Reload, or Develop › Restart Pipkin Server." spinner:NO];
}

- (void)webViewWebContentProcessDidTerminate:(WKWebView *)webView {
    AppLog(@"Web content process ended; reloading");
    [self loadApp];
}

#pragma mark UI

- (WKWebView *)webView:(WKWebView *)webView createWebViewWithConfiguration:(WKWebViewConfiguration *)configuration forNavigationAction:(WKNavigationAction *)action windowFeatures:(WKWindowFeatures *)features {
    NSURL *url = action.request.URL;
    if (url && ![self isAppURL:url] && ![self routeLink:url modifiers:action.modifierFlags]) [[NSWorkspace sharedWorkspace] openURL:url];
    return nil;
}

- (void)webView:(WKWebView *)webView runOpenPanelWithParameters:(WKOpenPanelParameters *)parameters initiatedByFrame:(WKFrameInfo *)frame completionHandler:(void (^)(NSArray<NSURL *> *))completion {
    NSOpenPanel *panel = [NSOpenPanel openPanel];
    panel.allowsMultipleSelection = parameters.allowsMultipleSelection;
    panel.canChooseDirectories = parameters.allowsDirectories;
    panel.canChooseFiles = YES;
    [panel beginSheetModalForWindow:self.window completionHandler:^(NSModalResponse result) {
        completion(result == NSModalResponseOK ? panel.URLs : nil);
    }];
}

- (void)webView:(WKWebView *)webView runJavaScriptAlertPanelWithMessage:(NSString *)message initiatedByFrame:(WKFrameInfo *)frame completionHandler:(void (^)(void))completion {
    NSAlert *alert = [NSAlert new];
    alert.messageText = message;
    [alert beginSheetModalForWindow:self.window completionHandler:^(NSModalResponse r) { completion(); }];
}

- (void)webView:(WKWebView *)webView runJavaScriptConfirmPanelWithMessage:(NSString *)message initiatedByFrame:(WKFrameInfo *)frame completionHandler:(void (^)(BOOL))completion {
    NSAlert *alert = [NSAlert new];
    alert.messageText = message;
    [alert addButtonWithTitle:@"OK"];
    [alert addButtonWithTitle:@"Cancel"];
    [alert beginSheetModalForWindow:self.window completionHandler:^(NSModalResponse r) { completion(r == NSAlertFirstButtonReturn); }];
}

- (void)userContentController:(WKUserContentController *)controller didReceiveScriptMessage:(WKScriptMessage *)message {
    if (!message.frameInfo.isMainFrame || ![self isAppURL:message.frameInfo.request.URL] ||
        ![message.body isKindOfClass:[NSDictionary class]]) return;
    NSDictionary *body = message.body;
    if ([body[@"type"] isEqual:@"theme"] && [body[@"background"] isKindOfClass:[NSString class]]) {
        NSColor *color = ParseCSSColor(body[@"background"]);
        if (color) self.window.backgroundColor = color;
        NSString *scheme = [body[@"scheme"] isKindOfClass:[NSString class]] ? body[@"scheme"] : nil;
        if ([scheme isEqual:@"dark"]) self.window.appearance = [NSAppearance appearanceNamed:NSAppearanceNameDarkAqua];
        else if ([scheme isEqual:@"light"]) self.window.appearance = [NSAppearance appearanceNamed:NSAppearanceNameAqua];
        else self.window.appearance = nil;
        return;
    }
    if ([body[@"type"] isEqual:@"browser"]) {
        if ([body[@"action"] isEqual:@"toggle"]) [self toggleBrowser];
        else if ([body[@"action"] isEqual:@"show"]) [self showBrowser:YES];
        else if ([body[@"action"] isEqual:@"hide"]) [self showBrowser:NO];
        else if ([body[@"action"] isEqual:@"navigate"] && [body[@"query"] isKindOfClass:[NSString class]]) {
            [self showBrowser:YES];
            [self.browser openInput:body[@"query"]];
        }
        else if ([body[@"action"] isEqual:@"agent"] && [body[@"id"] isKindOfClass:[NSString class]]) {
            [self agentBrowserRequest:body];
        }
        else if ([body[@"action"] isEqual:@"open"] && [body[@"url"] isKindOfClass:[NSString class]]) {
            NSURL *url = [NSURL URLWithString:body[@"url"]];
            NSString *scheme = url.scheme.lowercaseString;
            if ([scheme isEqualToString:@"http"] || [scheme isEqualToString:@"https"]) [self openInBrowser:url];
        }
        return;
    }
    [self.owner pageMessage:body from:self];
}

- (BOOL)windowShouldClose:(NSWindow *)sender {
    if (!self.isMain) return YES;
    [self.owner closeMainWindow];
    return NO;
}

- (void)windowWillClose:(NSNotification *)notification {
    [self.webView.configuration.userContentController removeScriptMessageHandlerForName:@"pipkin"];
    [self.webView loadHTMLString:@"" baseURL:nil];  // drops the server session so the model can unload
    [self.owner windowClosed:self];
}

@end

#pragma mark - App

@implementation AppDelegate

- (void)applicationWillFinishLaunching:(NSNotification *)notification {
    [[NSAppleEventManager sharedAppleEventManager] setEventHandler:self
                                                       andSelector:@selector(handleURL:withReply:)
                                                     forEventClass:kInternetEventClass
                                                        andEventID:kAEGetURL];
}

- (void)applicationDidFinishLaunching:(NSNotification *)notification {
    NSString *candidate = [[[NSBundle mainBundle].bundlePath stringByDeletingLastPathComponent] stringByAppendingPathComponent:@"pipkin-dev"];
    if ([[NSFileManager defaultManager] fileExistsAtPath:[candidate stringByAppendingPathComponent:@"server.py"]]) self.devDir = candidate;
    [self buildMenus];
    UNUserNotificationCenter.currentNotificationCenter.delegate = self;

    self.main = [[PipkinWindow alloc] initWithPort:kMainPort title:@"Pipkin" autosave:@"PipkinMain" main:YES owner:self];
    NSImage *art = [[NSImage alloc] initWithContentsOfFile:[[self resourcesDir] stringByAppendingPathComponent:@"shell/Sprig.png"]];
    __weak typeof(self) weak = self;
    self.pet = [[PipkinPet alloc] initWithImage:art action:^(NSString *action) { [weak petAction:action]; }];
    self.pet.visible = ![[NSUserDefaults standardUserDefaults] boolForKey:@"PipkinPetHidden"];
    [self.main showStatus:@"Starting Pipkin" detail:@"Starting the local server…" spinner:YES];
    [self.main.window makeKeyAndOrderFront:nil];
    [NSApp activateIgnoringOtherApps:YES];
    [self startMainServerThenLoad];
}

- (NSString *)resourcesDir {
    return [NSBundle mainBundle].resourcePath;
}

- (NSTask *)launchServerIn:(NSString *)dir port:(NSInteger)port logName:(NSString *)logName {
    NSString *logPath = [LogDir() stringByAppendingPathComponent:logName];
    [[NSFileManager defaultManager] createFileAtPath:logPath contents:nil attributes:nil];
    NSFileHandle *log = [NSFileHandle fileHandleForWritingAtPath:logPath];
    NSTask *task = [NSTask new];
    task.executableURL = [NSURL fileURLWithPath:@"/usr/bin/python3"];
    task.arguments = @[@"-B", @"server.py", @"--serve", @"--port", [NSString stringWithFormat:@"%ld", (long)port]];
    task.currentDirectoryURL = [NSURL fileURLWithPath:dir];
    NSMutableDictionary *env = [[NSProcessInfo processInfo].environment mutableCopy];
    env[@"PATH"] = [@"/opt/homebrew/bin:/usr/local/bin:" stringByAppendingString:env[@"PATH"] ?: @"/usr/bin:/bin:/usr/sbin:/sbin"];
    env[@"PYTHONUNBUFFERED"] = @"1";
    task.environment = env;
    task.standardInput = [NSFileHandle fileHandleWithNullDevice];
    task.standardOutput = log;
    task.standardError = log;
    NSError *error = nil;
    if (![task launchAndReturnError:&error]) {
        AppLog(@"Could not start server in %@: %@", dir, error.localizedDescription);
        return nil;
    }
    AppLog(@"Started server pid %d on port %ld from %@", task.processIdentifier, (long)port, dir);
    return task;
}

- (void)startMainServerThenLoad {
    NSUInteger generation = ++self.launchGeneration;
    self.mainReady = NO;
    CheckHealth(kMainPort, ^(BOOL running) {
        if (self.mainParked || generation != self.launchGeneration) return;
        if (running) {
            AppLog(@"Using the Pipkin server that is already running on %ld", (long)kMainPort);
            [self.main loadApp];
            return;
        }
        self.server = [self launchServerIn:[self resourcesDir] port:kMainPort logName:@"server.log"];
        WaitForHealth(kMainPort, [NSDate dateWithTimeIntervalSinceNow:20], ^(BOOL ok) {
            if (self.mainParked || generation != self.launchGeneration) return;
            if (ok) {
                [self.main loadApp];
            } else {
                [self.main showStatus:@"Pipkin could not start"
                               detail:@"The local server did not start. Port 8765 may be in use. See Develop › Show Logs."
                              spinner:NO];
            }
        });
    });
}

- (void)stopTask:(NSTask *)task {
    if (!task.running) return;
    [task interrupt];  // SIGINT: the server shuts down cleanly and stops any Ollama it started
    NSDate *deadline = [NSDate dateWithTimeIntervalSinceNow:3];
    while (task.running && [deadline timeIntervalSinceNow] > 0) [NSThread sleepForTimeInterval:0.05];
    if (task.running) [task terminate];
}

- (void)killExternalServerOnPort:(NSInteger)port {
    NSString *pattern = port == kMainPort ? @"server.py --serve$" : [NSString stringWithFormat:@"server.py --serve --port %ld$", (long)port];
    NSTask *pkill = [NSTask launchedTaskWithExecutableURL:[NSURL fileURLWithPath:@"/usr/bin/pkill"]
                                                arguments:@[@"-INT", @"-f", pattern] error:nil terminationHandler:nil];
    [pkill waitUntilExit];
    [NSThread sleepForTimeInterval:1.0];
}

#pragma mark Page messages

- (void)pageMessage:(NSDictionary *)message from:(PipkinWindow *)window {
    NSString *type = message[@"type"];
    if ([type isEqual:@"ready"] && window == self.main && !self.mainParked) {
        self.mainReady = YES;
        [self runPendingMainAction];
        return;
    }
    if ([type isEqual:@"toggle-pet"]) {
        [self togglePet:nil];
        return;
    }
    if ([type isEqual:@"apply-update"] && window == self.main) {
        [self applyUpdate];
        return;
    }
    if ([type isEqual:@"busy"]) {
        window.busy = [message[@"value"] boolValue];
        BOOL busy = self.main.busy || self.dev.busy;
        if (busy && !self.busyActivity) {
            self.busyActivity = [[NSProcessInfo processInfo] beginActivityWithOptions:NSActivityUserInitiatedAllowingIdleSystemSleep
                                                                               reason:@"Pipkin is generating a reply"];
        } else if (!busy && self.busyActivity) {
            [[NSProcessInfo processInfo] endActivity:self.busyActivity];
            self.busyActivity = nil;
        }
        if (busy) [self.pet setMood:@"thinking"];
        else if ([self.pet.mood isEqual:@"thinking"] || [self.pet.mood isEqual:@"attention"]) [self.pet setMood:@"idle"];
        return;
    }
    if ([type isEqual:@"attention"] || [type isEqual:@"finished"]) {
        BOOL urgent = [type isEqual:@"attention"];
        BOOL watching = NSApp.isActive && window.window.isKeyWindow;
        [self.pet setMood:urgent ? @"attention" : @"happy"];
        if (!watching) [self.pet say:urgent ? @"Pipkin needs your approval" : @"Done! Click to see the reply."];
        if (watching) return;
        NSApp.dockTile.badgeLabel = urgent ? @"!" : @"1";
        [NSApp requestUserAttention:urgent ? NSCriticalRequest : NSInformationalRequest];
        [self notifyTitle:message[@"title"] body:message[@"body"]];
    }
}

- (void)notifyTitle:(NSString *)title body:(NSString *)body {
    UNUserNotificationCenter *center = UNUserNotificationCenter.currentNotificationCenter;
    [center requestAuthorizationWithOptions:UNAuthorizationOptionAlert | UNAuthorizationOptionSound completionHandler:^(BOOL granted, NSError *error) {
        if (!granted) return;
        UNMutableNotificationContent *content = [UNMutableNotificationContent new];
        content.title = [title isKindOfClass:[NSString class]] ? title : @"Pipkin";
        content.body = [body isKindOfClass:[NSString class]] ? body : @"";
        content.sound = UNNotificationSound.defaultSound;
        UNNotificationRequest *request = [UNNotificationRequest requestWithIdentifier:[NSUUID UUID].UUIDString content:content trigger:nil];
        [center addNotificationRequest:request withCompletionHandler:nil];
    }];
}

- (void)userNotificationCenter:(UNUserNotificationCenter *)center didReceiveNotificationResponse:(UNNotificationResponse *)response withCompletionHandler:(void (^)(void))completion {
    dispatch_async(dispatch_get_main_queue(), ^{
        [self showMainWindowForAction:@"open"];
    });
    completion();
}

- (void)applicationDidBecomeActive:(NSNotification *)notification {
    NSApp.dockTile.badgeLabel = nil;
}

#pragma mark Lifecycle

- (BOOL)applicationShouldTerminateAfterLastWindowClosed:(NSApplication *)sender {
    return !self.pet.visible;
}

- (void)windowClosed:(PipkinWindow *)window {
    if (window == self.dev) {
        [self pageMessage:@{@"type": @"busy", @"value": @NO} from:window];
        self.dev = nil;
        if (self.devServer) {
            NSTask *task = self.devServer;
            self.devServer = nil;
            dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(0.5 * NSEC_PER_SEC)), dispatch_get_main_queue(), ^{ [self stopTask:task]; });
        }
    } else if (window == self.main) {
        [NSApp terminate:nil];
    }
}

- (BOOL)applicationShouldHandleReopen:(NSApplication *)sender hasVisibleWindows:(BOOL)visible {
    if (!visible || self.mainParked) [self showMainWindowForAction:@"open"];
    return YES;
}

- (void)handleURL:(NSAppleEventDescriptor *)event withReply:(NSAppleEventDescriptor *)reply {
    [self showMainWindowForAction:@"open"];
}

- (void)closeMainWindow {
    if (!self.pet.visible) {
        [NSApp terminate:nil];
        return;
    }
    if (self.mainParked || self.parkingMain) return;
    self.parkingMain = YES;
    [self.main.webView evaluateJavaScript:@"window.PipkinShell ? PipkinShell.isBusy() : false"
                      completionHandler:^(id result, NSError *error) {
        if ([result boolValue] || (error && self.main.busy)) {
            NSAlert *alert = [NSAlert new];
            alert.messageText = @"Tuck Pipkin away?";
            alert.informativeText = @"This stops the current reply or Work run and unloads the model.";
            [alert addButtonWithTitle:@"Tuck away"];
            [alert addButtonWithTitle:@"Cancel"];
            [alert beginSheetModalForWindow:self.main.window completionHandler:^(NSModalResponse choice) {
                if (choice == NSAlertFirstButtonReturn) [self parkMainWindow];
                else self.parkingMain = NO;
            }];
        } else [self parkMainWindow];
    }];
}

- (void)parkMainWindow {
    self.mainParked = YES;
    self.mainReady = NO;
    self.pendingMainAction = nil;
    self.launchGeneration++;
    [self.main.window orderOut:nil];
    [self.main.webView loadHTMLString:@"" baseURL:nil];
    [self pageMessage:@{@"type": @"busy", @"value": @NO} from:self.main];
    self.pet.docked = YES;
    NSTask *task = self.server;
    dispatch_async(dispatch_get_global_queue(QOS_CLASS_USER_INITIATED, 0), ^{
        [self stopTask:task];
        dispatch_async(dispatch_get_main_queue(), ^{
            if (self.server == task) self.server = nil;
            self.parkingMain = NO;
            if (self.pendingMainAction) [self showMainWindowForAction:self.pendingMainAction];
        });
    });
}

- (void)runPendingMainAction {
    if (!self.mainReady || !self.pendingMainAction) return;
    static NSDictionary<NSString *, NSString *> *commands;
    if (!commands) commands = @{
        @"chat": @"PipkinShell.setMode('chat')",
        @"work": @"PipkinShell.setMode('work')",
        @"new": @"PipkinShell.newChat()",
        @"skills": @"PipkinShell.openSkills()",
    };
    NSString *script = commands[self.pendingMainAction];
    if ([self.pendingMainAction isEqual:@"ask"] && self.pendingAskText) {
        NSData *json = [NSJSONSerialization dataWithJSONObject:self.pendingAskText options:NSJSONWritingFragmentsAllowed error:nil];
        NSString *text = json ? [[NSString alloc] initWithData:json encoding:NSUTF8StringEncoding] : nil;
        script = text ? [NSString stringWithFormat:@"PipkinShell.ask(%@)", text] : nil;
        self.pendingAskText = nil;
    }
    self.pendingMainAction = nil;
    if (script) [self.main runJS:script];
}

- (void)showMainWindowForAction:(NSString *)action {
    self.pendingMainAction = action;
    if (self.parkingMain && self.mainParked) return;
    if (self.mainParked) {
        self.mainParked = NO;
        self.pet.docked = NO;
        [self.main showStatus:@"Starting Pipkin" detail:@"Starting the local server…" spinner:YES];
        [self startMainServerThenLoad];
    }
    [NSApp activateIgnoringOtherApps:YES];
    [self.main.window makeKeyAndOrderFront:nil];
    [self runPendingMainAction];
}

- (void)petAction:(NSString *)action {
    if ([action isEqual:@"hide"]) {
        self.pet.visible = NO;
        [[NSUserDefaults standardUserDefaults] setBool:YES forKey:@"PipkinPetHidden"];
    } else if ([action isEqual:@"hide-temporarily"]) {
        self.pet.visible = NO;
        dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(3600 * NSEC_PER_SEC)), dispatch_get_main_queue(), ^{
            if (![[NSUserDefaults standardUserDefaults] boolForKey:@"PipkinPetHidden"]) self.pet.visible = YES;
        });
    } else if ([action hasPrefix:@"ask:"]) {
        self.pendingAskText = [action substringFromIndex:4];
        [self showMainWindowForAction:@"ask"];
    } else {
        [self showMainWindowForAction:action];
    }
}

/// Runs the helper the server staged after verifying a signed update, then quits so it can swap in the new
/// version and reopen Pipkin. The script path is fixed; nothing from the page chooses what runs.
- (void)applyUpdate {
    NSString *script = [NSHomeDirectory() stringByAppendingPathComponent:@"Library/Application Support/Pipkin/updates/apply-update.sh"];
    NSDictionary *attrs = [[NSFileManager defaultManager] attributesOfItemAtPath:script error:nil];
    if (!attrs || [attrs[NSFileOwnerAccountID] unsignedIntValue] != getuid() || ([attrs[NSFilePosixPermissions] unsignedShortValue] & 022)) {
        AppLog(@"No usable update helper at %@", script);
        return;
    }
    NSTask *task = [NSTask new];
    task.executableURL = [NSURL fileURLWithPath:@"/bin/sh"];
    task.arguments = @[script, [NSString stringWithFormat:@"%d", getpid()]];
    task.standardInput = [NSFileHandle fileHandleWithNullDevice];
    task.standardOutput = [NSFileHandle fileHandleWithNullDevice];
    task.standardError = [NSFileHandle fileHandleWithNullDevice];
    NSError *error = nil;
    if (![task launchAndReturnError:&error]) {
        AppLog(@"Could not start the update helper: %@", error.localizedDescription);
        return;
    }
    AppLog(@"Installing update; helper pid %d", task.processIdentifier);
    self.quitConfirmed = YES;
    [NSApp terminate:nil];
}

- (void)togglePet:(id)sender {
    self.pet.visible = !self.pet.visible;
    [[NSUserDefaults standardUserDefaults] setBool:!self.pet.visible forKey:@"PipkinPetHidden"];
}

- (NSApplicationTerminateReply)applicationShouldTerminate:(NSApplication *)sender {
    if (self.quitConfirmed || !self.main.webView) return NSTerminateNow;
    // A page that is suspended or stuck may never answer; don't let that block quitting.
    __block BOOL answered = NO;
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(3 * NSEC_PER_SEC)), dispatch_get_main_queue(), ^{
        if (answered) return;
        answered = YES;
        [NSApp replyToApplicationShouldTerminate:YES];
    });
    [self.main.webView evaluateJavaScript:@"window.PipkinShell ? window.PipkinShell.isBusy() : false" completionHandler:^(id result, NSError *error) {
        if (answered) return;
        answered = YES;
        if (![result boolValue]) {
            [NSApp replyToApplicationShouldTerminate:YES];
            return;
        }
        NSAlert *alert = [NSAlert new];
        alert.messageText = @"Pipkin is still working";
        alert.informativeText = @"Quitting stops the current reply or agent run and unloads the model.";
        [alert addButtonWithTitle:@"Quit"];
        [alert addButtonWithTitle:@"Cancel"];
        BOOL quit = [alert runModal] == NSAlertFirstButtonReturn;
        [NSApp replyToApplicationShouldTerminate:quit];
    }];
    return NSTerminateLater;
}

/// When Pipkin attached to a server it did not start, ask that server to unload the model (quitting must free it).
- (void)unloadExternalModel {
    NSURL *url = [NSURL URLWithString:[NSString stringWithFormat:@"http://127.0.0.1:%ld/api/model/unload", (long)kMainPort]];
    NSMutableURLRequest *request = [NSMutableURLRequest requestWithURL:url cachePolicy:NSURLRequestReloadIgnoringLocalCacheData timeoutInterval:6];
    request.HTTPMethod = @"POST";
    [request setValue:@"application/json" forHTTPHeaderField:@"Content-Type"];
    [request setValue:[NSString stringWithFormat:@"http://127.0.0.1:%ld", (long)kMainPort] forHTTPHeaderField:@"Origin"];
    dispatch_semaphore_t finished = dispatch_semaphore_create(0);
    [[[NSURLSession sharedSession] dataTaskWithRequest:request completionHandler:^(NSData *d, NSURLResponse *r, NSError *e) {
        AppLog(@"Asked the running Pipkin server to unload the model (status %ld)", (long)[(NSHTTPURLResponse *)r statusCode]);
        dispatch_semaphore_signal(finished);
    }] resume];
    dispatch_semaphore_wait(finished, dispatch_time(DISPATCH_TIME_NOW, (int64_t)(6.5 * NSEC_PER_SEC)));
}

- (void)applicationWillTerminate:(NSNotification *)notification {
    AppLog(@"Quitting");
    self.pet.visible = NO;
    if (!self.server.running && !self.mainParked) [self unloadExternalModel];
    [self stopTask:self.devServer];
    [self stopTask:self.server];
}

#pragma mark Menu actions

- (PipkinWindow *)current {
    if (self.dev && self.dev.window.isKeyWindow) return self.dev;
    return self.main;
}

- (void)newChat:(id)sender { [[self current] runJS:@"window.PipkinShell && PipkinShell.newChat()"]; }
- (void)openSettings:(id)sender { [[self current] runJS:@"window.PipkinShell && PipkinShell.openSettings()"]; }
- (void)toggleSidebar:(id)sender { [[self current] runJS:@"window.PipkinShell && PipkinShell.toggleSidebar()"]; }
- (void)toggleMode:(id)sender { [[self current] runJS:@"window.PipkinShell && PipkinShell.toggleMode()"]; }
- (void)stopReply:(id)sender { [[self current] runJS:@"window.PipkinShell && PipkinShell.stop()"]; }
- (void)exportChat:(id)sender { [[self current] runJS:@"window.PipkinShell && PipkinShell.exportChat()"]; }
- (void)exportData:(id)sender { [[self current] runJS:@"window.PipkinShell && PipkinShell.exportData()"]; }
- (void)importData:(id)sender { [[self current] runJS:@"window.PipkinShell && PipkinShell.importData()"]; }
- (void)reloadPage:(id)sender { [[self current] loadApp]; }
- (void)toggleBrowser:(id)sender { [[self current] toggleBrowser]; }
- (void)toggleTerminal:(id)sender { [[self current] runJS:@"window.PipkinPanels && PipkinPanels.toggleTerminal()"]; }
- (void)toggleLinksExternal:(id)sender {
    NSUserDefaults *defaults = [NSUserDefaults standardUserDefaults];
    [defaults setBool:![defaults boolForKey:@"LinksOpenExternally"] forKey:@"LinksOpenExternally"];
}

- (void)setZoom:(double)zoom {
    zoom = MAX(0.6, MIN(2.0, zoom));
    self.main.webView.pageZoom = zoom;
    self.dev.webView.pageZoom = zoom;
    [[NSUserDefaults standardUserDefaults] setDouble:zoom forKey:@"PageZoom"];
}
- (void)zoomIn:(id)sender { [self setZoom:[self current].webView.pageZoom + 0.1]; }
- (void)zoomOut:(id)sender { [self setZoom:[self current].webView.pageZoom - 0.1]; }
- (void)actualSize:(id)sender { [self setZoom:1.0]; }

- (void)restartMainServer:(id)sender {
    [self.main showStatus:@"Restarting Pipkin" detail:@"Restarting the local server…" spinner:YES];
    if (self.server) {
        [self stopTask:self.server];
        self.server = nil;
    } else {
        [self killExternalServerOnPort:kMainPort];
    }
    [self startMainServerThenLoad];
}

- (void)openDevWindow:(id)sender {
    if (!self.devDir) return;
    if (self.dev) {
        [self.dev.window makeKeyAndOrderFront:nil];
        return;
    }
    self.dev = [[PipkinWindow alloc] initWithPort:kDevPort title:@"Pipkin Dev" autosave:@"PipkinDev" main:NO owner:self];
    [self.dev showStatus:@"Starting Pipkin Dev" detail:@"Running the pipkin-dev copy on port 8766." spinner:YES];
    [self.dev.window makeKeyAndOrderFront:nil];
    [self startDevServerThenLoad];
}

- (void)startDevServerThenLoad {
    CheckHealth(kDevPort, ^(BOOL running) {
        if (running) {
            [self.dev loadApp];
            return;
        }
        self.devServer = [self launchServerIn:self.devDir port:kDevPort logName:@"dev-server.log"];
        WaitForHealth(kDevPort, [NSDate dateWithTimeIntervalSinceNow:20], ^(BOOL ok) {
            if (ok) [self.dev loadApp];
            else [self.dev showStatus:@"Pipkin Dev could not start" detail:@"See Develop › Show Logs (dev-server.log)." spinner:NO];
        });
    });
}

- (void)restartDevServer:(id)sender {
    if (!self.devDir) return;
    if (!self.dev) {
        [self openDevWindow:sender];
        return;
    }
    [self.dev showStatus:@"Restarting Pipkin Dev" detail:@"Loading your latest server.py and agent.py changes…" spinner:YES];
    if (self.devServer) {
        [self stopTask:self.devServer];
        self.devServer = nil;
    } else {
        [self killExternalServerOnPort:kDevPort];
    }
    [self startDevServerThenLoad];
}

- (void)promoteDev:(id)sender {
    if (!self.devDir) return;
    NSAlert *confirm = [NSAlert new];
    confirm.messageText = @"Promote pipkin-dev into Pipkin?";
    confirm.informativeText = @"This checks the dev copy, backs up the current app to backups/, then copies server.py, agent.py and dist/ into Pipkin.app and restarts the server.";
    [confirm addButtonWithTitle:@"Promote"];
    [confirm addButtonWithTitle:@"Cancel"];
    if ([confirm runModal] != NSAlertFirstButtonReturn) return;

    NSTask *task = [NSTask new];
    task.executableURL = [NSURL fileURLWithPath:@"/bin/sh"];
    task.arguments = @[[self.devDir stringByAppendingPathComponent:@"Promote to Pipkin.command"], @"--yes"];
    task.currentDirectoryURL = [NSURL fileURLWithPath:self.devDir];
    NSMutableDictionary *env = [[NSProcessInfo processInfo].environment mutableCopy];
    env[@"PIPKIN_APP"] = [NSBundle mainBundle].bundlePath;
    env[@"PATH"] = [@"/opt/homebrew/bin:/usr/local/bin:" stringByAppendingString:env[@"PATH"] ?: @"/usr/bin:/bin"];
    task.environment = env;
    NSPipe *pipe = [NSPipe pipe];
    task.standardOutput = pipe;
    task.standardError = pipe;
    NSError *error = nil;
    if (![task launchAndReturnError:&error]) {
        [[NSAlert alertWithError:error] runModal];
        return;
    }
    NSData *output = [pipe.fileHandleForReading readDataToEndOfFile];
    [task waitUntilExit];
    NSString *text = [[NSString alloc] initWithData:output encoding:NSUTF8StringEncoding] ?: @"";
    AppLog(@"Promote finished with %d:\n%@", task.terminationStatus, text);
    NSAlert *result = [NSAlert new];
    result.messageText = task.terminationStatus == 0 ? @"Promoted" : @"Promote failed";
    result.informativeText = text.length > 1500 ? [text substringFromIndex:text.length - 1500] : text;
    [result runModal];
    if (task.terminationStatus == 0) [self restartMainServer:nil];
}

- (void)revealDev:(id)sender {
    if (self.devDir) [[NSWorkspace sharedWorkspace] selectFile:[self.devDir stringByAppendingPathComponent:@"PIPKIN.md"] inFileViewerRootedAtPath:@""];
}

- (void)showLogs:(id)sender {
    [[NSWorkspace sharedWorkspace] openURL:[NSURL fileURLWithPath:LogDir()]];
}

- (BOOL)validateMenuItem:(NSMenuItem *)item {
    if (item.action == @selector(toggleLinksExternal:)) {
        item.state = [[NSUserDefaults standardUserDefaults] boolForKey:@"LinksOpenExternally"] ? NSControlStateValueOff : NSControlStateValueOn;
    }
    if (item.action == @selector(openDevWindow:) || item.action == @selector(restartDevServer:) ||
        item.action == @selector(promoteDev:) || item.action == @selector(revealDev:)) return self.devDir != nil;
    return YES;
}

#pragma mark Menus

- (NSMenuItem *)item:(NSString *)title action:(SEL)action key:(NSString *)key mods:(NSEventModifierFlags)mods target:(id)target {
    NSMenuItem *item = [[NSMenuItem alloc] initWithTitle:title action:action keyEquivalent:key ?: @""];
    item.keyEquivalentModifierMask = mods;
    item.target = target;
    return item;
}

- (NSMenu *)addMenu:(NSString *)title to:(NSMenu *)bar {
    NSMenuItem *holder = [[NSMenuItem alloc] initWithTitle:title action:nil keyEquivalent:@""];
    NSMenu *menu = [[NSMenu alloc] initWithTitle:title];
    holder.submenu = menu;
    [bar addItem:holder];
    return menu;
}

- (void)buildMenus {
    NSEventModifierFlags cmd = NSEventModifierFlagCommand, shift = NSEventModifierFlagShift, opt = NSEventModifierFlagOption;
    NSMenu *bar = [NSMenu new];

    NSMenu *app = [self addMenu:@"Pipkin" to:bar];
    [app addItem:[self item:@"About Pipkin" action:@selector(orderFrontStandardAboutPanel:) key:nil mods:0 target:NSApp]];
    [app addItem:[NSMenuItem separatorItem]];
    [app addItem:[self item:@"Settings…" action:@selector(openSettings:) key:@"," mods:cmd target:self]];
    [app addItem:[NSMenuItem separatorItem]];
    [app addItem:[self item:@"Hide Pipkin" action:@selector(hide:) key:@"h" mods:cmd target:NSApp]];
    [app addItem:[self item:@"Hide Others" action:@selector(hideOtherApplications:) key:@"h" mods:cmd | opt target:NSApp]];
    [app addItem:[self item:@"Show All" action:@selector(unhideAllApplications:) key:nil mods:0 target:NSApp]];
    [app addItem:[NSMenuItem separatorItem]];
    [app addItem:[self item:@"Quit Pipkin" action:@selector(terminate:) key:@"q" mods:cmd target:NSApp]];

    NSMenu *file = [self addMenu:@"File" to:bar];
    [file addItem:[self item:@"New Chat" action:@selector(newChat:) key:@"n" mods:cmd target:self]];
    [file addItem:[NSMenuItem separatorItem]];
    [file addItem:[self item:@"Export Chat as Markdown…" action:@selector(exportChat:) key:@"e" mods:cmd | shift target:self]];
    [file addItem:[self item:@"Export All Data…" action:@selector(exportData:) key:nil mods:0 target:self]];
    [file addItem:[self item:@"Import Data…" action:@selector(importData:) key:nil mods:0 target:self]];
    [file addItem:[NSMenuItem separatorItem]];
    [file addItem:[self item:@"Close Window" action:@selector(performClose:) key:@"w" mods:cmd target:nil]];

    NSMenu *edit = [self addMenu:@"Edit" to:bar];
    [edit addItem:[self item:@"Undo" action:NSSelectorFromString(@"undo:") key:@"z" mods:cmd target:nil]];
    [edit addItem:[self item:@"Redo" action:NSSelectorFromString(@"redo:") key:@"z" mods:cmd | shift target:nil]];
    [edit addItem:[NSMenuItem separatorItem]];
    [edit addItem:[self item:@"Cut" action:@selector(cut:) key:@"x" mods:cmd target:nil]];
    [edit addItem:[self item:@"Copy" action:@selector(copy:) key:@"c" mods:cmd target:nil]];
    [edit addItem:[self item:@"Paste" action:@selector(paste:) key:@"v" mods:cmd target:nil]];
    [edit addItem:[self item:@"Paste and Match Style" action:@selector(pasteAsPlainText:) key:@"v" mods:cmd | opt | shift target:nil]];
    [edit addItem:[self item:@"Select All" action:@selector(selectAll:) key:@"a" mods:cmd target:nil]];

    NSMenu *view = [self addMenu:@"View" to:bar];
    [view addItem:[self item:@"Toggle Sidebar" action:@selector(toggleSidebar:) key:@"s" mods:cmd | shift target:self]];
    [view addItem:[self item:@"Switch Chat / Agent" action:@selector(toggleMode:) key:@"a" mods:cmd | shift target:self]];
    [view addItem:[self item:@"Stop Reply" action:@selector(stopReply:) key:@"." mods:cmd target:self]];
    [view addItem:[self item:@"Show or Hide Desktop Pet" action:@selector(togglePet:) key:nil mods:0 target:self]];
    [view addItem:[NSMenuItem separatorItem]];
    [view addItem:[self item:@"Toggle Browser" action:@selector(toggleBrowser:) key:@"b" mods:cmd | shift target:self]];
    [view addItem:[self item:@"Toggle Terminal" action:@selector(toggleTerminal:) key:@"`" mods:NSEventModifierFlagControl target:self]];
    [view addItem:[self item:@"Open Links in Pipkin's Browser" action:@selector(toggleLinksExternal:) key:nil mods:0 target:self]];
    [view addItem:[NSMenuItem separatorItem]];
    [view addItem:[self item:@"Actual Size" action:@selector(actualSize:) key:@"0" mods:cmd target:self]];
    [view addItem:[self item:@"Zoom In" action:@selector(zoomIn:) key:@"=" mods:cmd target:self]];
    [view addItem:[self item:@"Zoom Out" action:@selector(zoomOut:) key:@"-" mods:cmd target:self]];
    [view addItem:[NSMenuItem separatorItem]];
    [view addItem:[self item:@"Reload" action:@selector(reloadPage:) key:@"r" mods:cmd target:self]];
    [view addItem:[self item:@"Enter Full Screen" action:@selector(toggleFullScreen:) key:@"f" mods:cmd | NSEventModifierFlagControl target:nil]];

    NSMenu *develop = [self addMenu:@"Develop" to:bar];
    [develop addItem:[self item:@"Open Pipkin Dev Window" action:@selector(openDevWindow:) key:@"d" mods:cmd | opt target:self]];
    [develop addItem:[self item:@"Restart Dev Server" action:@selector(restartDevServer:) key:@"r" mods:cmd | opt target:self]];
    [develop addItem:[self item:@"Promote Dev to Pipkin…" action:@selector(promoteDev:) key:nil mods:0 target:self]];
    [develop addItem:[NSMenuItem separatorItem]];
    [develop addItem:[self item:@"Restart Pipkin Server" action:@selector(restartMainServer:) key:nil mods:0 target:self]];
    [develop addItem:[self item:@"Reveal pipkin-dev in Finder" action:@selector(revealDev:) key:nil mods:0 target:self]];
    [develop addItem:[self item:@"Show Logs" action:@selector(showLogs:) key:nil mods:0 target:self]];

    NSMenu *window = [self addMenu:@"Window" to:bar];
    [window addItem:[self item:@"Minimize" action:@selector(performMiniaturize:) key:@"m" mods:cmd target:nil]];
    [window addItem:[self item:@"Zoom" action:@selector(performZoom:) key:nil mods:0 target:nil]];
    [window addItem:[NSMenuItem separatorItem]];
    [window addItem:[self item:@"Bring All to Front" action:@selector(arrangeInFront:) key:nil mods:0 target:NSApp]];

    NSApp.mainMenu = bar;
    NSApp.windowsMenu = window;
}

@end

int main(int argc, const char *argv[]) {
    @autoreleasepool {
        NSApplication *app = [NSApplication sharedApplication];
        AppDelegate *delegate = [AppDelegate new];
        app.delegate = delegate;
        [app setActivationPolicy:NSApplicationActivationPolicyRegular];
        [app run];
    }
    return 0;
}
