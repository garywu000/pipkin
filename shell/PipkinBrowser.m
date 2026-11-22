#import "PipkinBrowser.h"

static NSString *const kHome = @"https://www.google.com/";
static NSString *const kSearch = @"https://www.google.com/search";
static NSArray<NSString *> *ObservedKeys(void) {
    return @[@"title", @"URL", @"canGoBack", @"canGoForward", @"loading", @"estimatedProgress"];
}

#pragma mark - Tab button

@interface PipkinTabButton : NSView
@property (strong) NSTextField *label;
@property (strong) NSButton *close;
@property (assign, nonatomic) BOOL selected;
@property (assign) BOOL hovering;
@property (copy) void (^onSelect)(void);
@property (copy) void (^onClose)(void);
@end

@implementation PipkinTabButton

- (instancetype)initWithFrame:(NSRect)frame {
    self = [super initWithFrame:frame];
    if (!self) return nil;
    _label = [NSTextField labelWithString:@"New Tab"];
    _label.font = [NSFont systemFontOfSize:11.5];
    _label.lineBreakMode = NSLineBreakByTruncatingTail;
    _label.textColor = NSColor.secondaryLabelColor;
    [_label setContentCompressionResistancePriority:NSLayoutPriorityDefaultLow forOrientation:NSLayoutConstraintOrientationHorizontal];
    _close = [NSButton buttonWithImage:[NSImage imageWithSystemSymbolName:@"xmark" accessibilityDescription:@"Close tab"]
                                target:self action:@selector(closeTab:)];
    _close.bordered = NO;
    _close.imageScaling = NSImageScaleProportionallyDown;
    _close.contentTintColor = NSColor.secondaryLabelColor;
    _close.toolTip = @"Close tab (⌘W)";
    for (NSView *view in @[_label, _close]) {
        view.translatesAutoresizingMaskIntoConstraints = NO;
        [self addSubview:view];
    }
    [NSLayoutConstraint activateConstraints:@[
        [_label.leadingAnchor constraintEqualToAnchor:self.leadingAnchor constant:10],
        [_label.centerYAnchor constraintEqualToAnchor:self.centerYAnchor],
        [_close.leadingAnchor constraintEqualToAnchor:_label.trailingAnchor constant:2],
        [_close.trailingAnchor constraintEqualToAnchor:self.trailingAnchor constant:-5],
        [_close.centerYAnchor constraintEqualToAnchor:self.centerYAnchor],
        [_close.widthAnchor constraintEqualToConstant:16],
        [_close.heightAnchor constraintEqualToConstant:16],
    ]];
    [self addTrackingArea:[[NSTrackingArea alloc] initWithRect:NSZeroRect
        options:NSTrackingMouseEnteredAndExited | NSTrackingActiveInKeyWindow | NSTrackingInVisibleRect owner:self userInfo:nil]];
    return self;
}

- (void)setSelected:(BOOL)selected {
    _selected = selected;
    self.label.textColor = selected ? NSColor.labelColor : NSColor.secondaryLabelColor;
    [self updateClose];
    self.needsDisplay = YES;
}

- (void)updateClose {
    self.close.alphaValue = (self.selected || self.hovering) ? 1 : 0;
}

- (void)mouseEntered:(NSEvent *)event { self.hovering = YES; [self updateClose]; self.needsDisplay = YES; }
- (void)mouseExited:(NSEvent *)event { self.hovering = NO; [self updateClose]; self.needsDisplay = YES; }

- (void)mouseDown:(NSEvent *)event {
    if (event.buttonNumber == 2 || (event.modifierFlags & NSEventModifierFlagOption)) return;
    if (self.onSelect) self.onSelect();
}

- (void)otherMouseUp:(NSEvent *)event {
    if (event.buttonNumber == 2) [self closeTab:nil];  // middle-click closes, like Chrome
}

- (void)closeTab:(id)sender { if (self.onClose) self.onClose(); }

- (void)drawRect:(NSRect)dirtyRect {
    if (!self.selected && !self.hovering) return;
    NSRect box = NSInsetRect(self.bounds, 1, 3);
    NSColor *fill = self.selected ? [NSColor.labelColor colorWithAlphaComponent:0.09] : [NSColor.labelColor colorWithAlphaComponent:0.045];
    [fill setFill];
    [[NSBezierPath bezierPathWithRoundedRect:box xRadius:7 yRadius:7] fill];
}

@end

#pragma mark - Tab strip (lays tabs out like Chrome: equal widths, capped)

@interface PipkinTabStrip : NSView
@property (strong) NSMutableArray<PipkinTabButton *> *tabs;
@property (strong) NSButton *add;
@end

@implementation PipkinTabStrip

- (instancetype)initWithFrame:(NSRect)frame {
    self = [super initWithFrame:frame];
    if (self) _tabs = [NSMutableArray array];
    return self;
}

- (BOOL)isFlipped { return YES; }

- (void)layout {
    [super layout];
    CGFloat height = self.bounds.size.height, plus = 28, inset = 6;
    CGFloat available = self.bounds.size.width - plus - inset * 2;
    CGFloat width = self.tabs.count ? MIN(200, MAX(44, available / self.tabs.count)) : 0;
    CGFloat x = inset;
    for (PipkinTabButton *tab in self.tabs) {
        tab.frame = NSMakeRect(x, 0, width, height);
        tab.label.hidden = width < 64;
        x += width;
    }
    self.add.frame = NSMakeRect(MIN(x + 2, self.bounds.size.width - plus - inset), (height - 24) / 2, plus - 4, 24);
}

@end

#pragma mark - Pane

@interface PipkinBrowserPane ()
@property (strong) NSMutableArray<WKWebView *> *pages;
@property (strong) PipkinTabStrip *strip;
@property (strong) NSView *content;
@property (strong) NSTextField *address;
@property (strong) NSButton *back;
@property (strong) NSButton *forward;
@property (strong) NSButton *reload;
@property (strong) NSProgressIndicator *progress;
@property (assign) NSInteger current;
@end

@implementation PipkinBrowserPane

static NSButton *ToolButton(NSString *symbol, NSString *tip, id target, SEL action) {
    NSImage *image = [NSImage imageWithSystemSymbolName:symbol accessibilityDescription:tip];
    NSButton *button = [NSButton buttonWithImage:image target:target action:action];
    button.bezelStyle = NSBezelStyleTexturedRounded;
    button.bordered = NO;
    button.toolTip = tip;
    button.contentTintColor = NSColor.secondaryLabelColor;
    button.translatesAutoresizingMaskIntoConstraints = NO;
    [button.widthAnchor constraintEqualToConstant:26].active = YES;
    [button.heightAnchor constraintEqualToConstant:24].active = YES;
    return button;
}

- (instancetype)initWithFrame:(NSRect)frame {
    self = [super initWithFrame:frame];
    if (!self) return nil;
    _pages = [NSMutableArray array];
    _current = -1;

    _strip = [[PipkinTabStrip alloc] initWithFrame:NSZeroRect];
    _strip.translatesAutoresizingMaskIntoConstraints = NO;
    _strip.add = [NSButton buttonWithImage:[NSImage imageWithSystemSymbolName:@"plus" accessibilityDescription:@"New tab"]
                                    target:self action:@selector(newTab)];
    _strip.add.bordered = NO;
    _strip.add.toolTip = @"New tab (⌘T)";
    _strip.add.contentTintColor = NSColor.secondaryLabelColor;
    [_strip addSubview:_strip.add];

    _back = ToolButton(@"chevron.left", @"Back (⌘[)", self, @selector(goBack:));
    _forward = ToolButton(@"chevron.right", @"Forward (⌘])", self, @selector(goForward:));
    _reload = ToolButton(@"arrow.clockwise", @"Reload (⌘R)", self, @selector(reloadOrStop:));
    NSButton *external = ToolButton(@"safari", @"Open in your default browser", self, @selector(openExternally:));
    NSButton *close = ToolButton(@"sidebar.right", @"Hide browser (⌘⇧B)", self, @selector(closePane:));

    _address = [NSTextField new];
    _address.placeholderString = @"Search Google or type a URL";
    _address.bezelStyle = NSTextFieldRoundedBezel;
    _address.font = [NSFont systemFontOfSize:12.5];
    _address.lineBreakMode = NSLineBreakByTruncatingTail;
    _address.cell.scrollable = YES;
    _address.delegate = self;
    _address.target = self;
    _address.action = @selector(addressEntered:);
    _address.translatesAutoresizingMaskIntoConstraints = NO;

    _progress = [NSProgressIndicator new];
    _progress.style = NSProgressIndicatorStyleBar;
    _progress.indeterminate = NO;
    _progress.minValue = 0;
    _progress.maxValue = 1;
    _progress.controlSize = NSControlSizeSmall;
    _progress.hidden = YES;
    _progress.translatesAutoresizingMaskIntoConstraints = NO;

    NSStackView *bar = [NSStackView stackViewWithViews:@[_back, _forward, _reload, _address, external, close]];
    bar.orientation = NSUserInterfaceLayoutOrientationHorizontal;
    bar.spacing = 4;
    bar.edgeInsets = NSEdgeInsetsMake(2, 8, 6, 8);
    bar.translatesAutoresizingMaskIntoConstraints = NO;
    [_address setContentHuggingPriority:NSLayoutPriorityDefaultLow forOrientation:NSLayoutConstraintOrientationHorizontal];

    NSBox *rule = [NSBox new];
    rule.boxType = NSBoxSeparator;
    rule.translatesAutoresizingMaskIntoConstraints = NO;

    _content = [NSView new];
    _content.translatesAutoresizingMaskIntoConstraints = NO;

    for (NSView *view in @[_strip, bar, rule, _content, _progress]) [self addSubview:view];
    [NSLayoutConstraint activateConstraints:@[
        [_strip.topAnchor constraintEqualToAnchor:self.topAnchor constant:2],
        [_strip.leadingAnchor constraintEqualToAnchor:self.leadingAnchor],
        [_strip.trailingAnchor constraintEqualToAnchor:self.trailingAnchor],
        [_strip.heightAnchor constraintEqualToConstant:32],
        [bar.topAnchor constraintEqualToAnchor:_strip.bottomAnchor],
        [bar.leadingAnchor constraintEqualToAnchor:self.leadingAnchor],
        [bar.trailingAnchor constraintEqualToAnchor:self.trailingAnchor],
        [rule.topAnchor constraintEqualToAnchor:bar.bottomAnchor],
        [rule.leadingAnchor constraintEqualToAnchor:self.leadingAnchor],
        [rule.trailingAnchor constraintEqualToAnchor:self.trailingAnchor],
        [_progress.topAnchor constraintEqualToAnchor:rule.bottomAnchor constant:-1],
        [_progress.leadingAnchor constraintEqualToAnchor:self.leadingAnchor],
        [_progress.trailingAnchor constraintEqualToAnchor:self.trailingAnchor],
        [_progress.heightAnchor constraintEqualToConstant:3],
        [_content.topAnchor constraintEqualToAnchor:rule.bottomAnchor],
        [_content.leadingAnchor constraintEqualToAnchor:self.leadingAnchor],
        [_content.trailingAnchor constraintEqualToAnchor:self.trailingAnchor],
        [_content.bottomAnchor constraintEqualToAnchor:self.bottomAnchor],
    ]];
    return self;
}

- (void)dealloc {
    for (WKWebView *page in _pages) {
        for (NSString *key in ObservedKeys()) [page removeObserver:self forKeyPath:key];
    }
}

- (WKWebView *)webView {
    return self.current >= 0 && self.current < (NSInteger)self.pages.count ? self.pages[self.current] : nil;
}

#pragma mark Tabs

- (WKWebView *)addTabWithConfiguration:(WKWebViewConfiguration *)given select:(BOOL)select {
    WKWebViewConfiguration *config = given;
    if (!config) {
        config = [WKWebViewConfiguration new];
        config.websiteDataStore = [WKWebsiteDataStore defaultDataStore];
        @try { [config.preferences setValue:@YES forKey:@"developerExtrasEnabled"]; } @catch (NSException *e) {}
    }
    WKWebView *page = [[WKWebView alloc] initWithFrame:NSZeroRect configuration:config];
    page.navigationDelegate = self;
    page.UIDelegate = self;
    page.allowsBackForwardNavigationGestures = YES;
    page.allowsMagnification = YES;
    if (@available(macOS 13.3, *)) page.inspectable = YES;
    page.translatesAutoresizingMaskIntoConstraints = NO;
    page.hidden = YES;
    [self.content addSubview:page];
    [NSLayoutConstraint activateConstraints:@[
        [page.topAnchor constraintEqualToAnchor:self.content.topAnchor],
        [page.leadingAnchor constraintEqualToAnchor:self.content.leadingAnchor],
        [page.trailingAnchor constraintEqualToAnchor:self.content.trailingAnchor],
        [page.bottomAnchor constraintEqualToAnchor:self.content.bottomAnchor],
    ]];
    for (NSString *key in ObservedKeys()) [page addObserver:self forKeyPath:key options:0 context:nil];

    NSInteger insertAt = self.current >= 0 ? self.current + 1 : (NSInteger)self.pages.count;
    [self.pages insertObject:page atIndex:insertAt];
    PipkinTabButton *tab = [[PipkinTabButton alloc] initWithFrame:NSZeroRect];
    __weak PipkinBrowserPane *weakSelf = self;
    __weak WKWebView *weakPage = page;
    tab.onSelect = ^{ [weakSelf selectPage:weakPage]; };
    tab.onClose = ^{ [weakSelf closePage:weakPage]; };
    [self.strip.tabs insertObject:tab atIndex:insertAt];
    [self.strip addSubview:tab];
    if (select || self.current < 0) [self selectIndex:insertAt];
    else if (insertAt <= self.current) self.current += 1;
    self.strip.needsLayout = YES;
    return page;
}

- (void)selectPage:(WKWebView *)page {
    NSUInteger index = [self.pages indexOfObject:page];
    if (index != NSNotFound) [self selectIndex:index];
}

- (void)selectIndex:(NSInteger)index {
    if (index < 0 || index >= (NSInteger)self.pages.count) return;
    self.current = index;
    for (NSInteger i = 0; i < (NSInteger)self.pages.count; i++) {
        self.pages[i].hidden = i != index;
        self.strip.tabs[i].selected = i == index;
    }
    [self refreshToolbar];
    if (self.window.firstResponder != self.address.currentEditor) {
        self.address.stringValue = self.webView.URL.absoluteString ?: @"";
    }
}

- (void)closePage:(WKWebView *)page {
    NSUInteger index = [self.pages indexOfObject:page];
    if (index == NSNotFound) return;
    for (NSString *key in ObservedKeys()) [page removeObserver:self forKeyPath:key];
    [page stopLoading];
    [page removeFromSuperview];
    [self.pages removeObjectAtIndex:index];
    [self.strip.tabs[index] removeFromSuperview];
    [self.strip.tabs removeObjectAtIndex:index];
    self.strip.needsLayout = YES;
    if (!self.pages.count) {
        self.current = -1;
        if (self.onClose) self.onClose();
        return;
    }
    NSInteger next = (NSInteger)index <= self.current ? MAX(0, self.current - 1) : self.current;
    if ((NSInteger)index == self.current) next = MIN((NSInteger)index, (NSInteger)self.pages.count - 1);
    self.current = -1;
    [self selectIndex:next];
}

- (void)newTab {
    [self addTabWithConfiguration:nil select:YES];
    [self.webView loadRequest:[NSURLRequest requestWithURL:[NSURL URLWithString:kHome]]];
    [self focusAddressBar];
}

- (void)closeCurrentTab {
    if (self.webView) [self closePage:self.webView];
}

- (void)openURL:(NSURL *)url {
    if (!url) return;
    if (!self.webView) [self addTabWithConfiguration:nil select:YES];
    [self.webView loadRequest:[NSURLRequest requestWithURL:url]];
}

- (void)openURLInNewTab:(NSURL *)url {
    if (!url) return;
    [self addTabWithConfiguration:nil select:YES];
    [self.webView loadRequest:[NSURLRequest requestWithURL:url]];
}

- (void)loadHomeIfEmpty {
    if (!self.pages.count) [self openURL:[NSURL URLWithString:kHome]];
}

- (void)focusAddressBar {
    [self.window makeFirstResponder:self.address];
    [self.address selectText:nil];
}

#pragma mark Toolbar state

- (void)refreshToolbar {
    WKWebView *page = self.webView;
    self.back.enabled = page.canGoBack;
    self.forward.enabled = page.canGoForward;
    BOOL loading = page.loading;
    self.reload.image = [NSImage imageWithSystemSymbolName:loading ? @"xmark" : @"arrow.clockwise" accessibilityDescription:loading ? @"Stop" : @"Reload"];
    self.reload.toolTip = loading ? @"Stop" : @"Reload (⌘R)";
    self.progress.hidden = !loading;
    self.progress.doubleValue = page.estimatedProgress;
}

- (void)observeValueForKeyPath:(NSString *)keyPath ofObject:(id)object change:(NSDictionary *)change context:(void *)context {
    WKWebView *page = object;
    NSUInteger index = [self.pages indexOfObject:page];
    if (index == NSNotFound) return;
    if ([keyPath isEqual:@"title"] || [keyPath isEqual:@"URL"]) {
        NSString *title = page.title.length ? page.title : (page.URL.host ?: @"New Tab");
        self.strip.tabs[index].label.stringValue = title;
        self.strip.tabs[index].toolTip = title;
    }
    if (page != self.webView) return;
    [self refreshToolbar];
    if ([keyPath isEqual:@"URL"] && self.window.firstResponder != self.address.currentEditor) {
        self.address.stringValue = page.URL.absoluteString ?: @"";
    }
}

#pragma mark Actions

- (void)goBack:(id)sender { [self.webView goBack]; }
- (void)goForward:(id)sender { [self.webView goForward]; }
- (void)reloadOrStop:(id)sender {
    if (self.webView.loading) [self.webView stopLoading];
    else [self.webView reload];
}
- (void)openExternally:(id)sender {
    if (self.webView.URL) [[NSWorkspace sharedWorkspace] openURL:self.webView.URL];
}
- (void)closePane:(id)sender { if (self.onClose) self.onClose(); }

/// Turns what was typed into a URL: an address if it looks like one, otherwise a Google search.
- (NSURL *)urlForInput:(NSString *)text {
    NSURLComponents *parts = [NSURLComponents componentsWithString:text];
    BOOL hasScheme = [@[@"http", @"https"] containsObject:parts.scheme.lowercaseString ?: @""];
    BOOL looksLikeHost = ![text containsString:@" "] && ([text containsString:@"."] || [text hasPrefix:@"localhost"]);
    if (hasScheme) return parts.URL;
    if (looksLikeHost) {
        BOOL local = [text hasPrefix:@"localhost"] || [text hasPrefix:@"127.0.0.1"];
        NSURL *url = [NSURL URLWithString:[(local ? @"http://" : @"https://") stringByAppendingString:text]];
        if (url) return url;
    }
    NSURLComponents *search = [NSURLComponents componentsWithString:kSearch];
    search.queryItems = @[[NSURLQueryItem queryItemWithName:@"q" value:text]];
    return search.URL;
}

- (void)addressEntered:(id)sender {
    NSString *text = [self.address.stringValue stringByTrimmingCharactersInSet:NSCharacterSet.whitespaceAndNewlineCharacterSet];
    if (!text.length) return;
    [self openURL:[self urlForInput:text]];
    [self.window makeFirstResponder:self.webView];
}

- (BOOL)control:(NSControl *)control textView:(NSTextView *)textView doCommandBySelector:(SEL)command {
    if (command == @selector(cancelOperation:)) {
        self.address.stringValue = self.webView.URL.absoluteString ?: @"";
        [self.window makeFirstResponder:self.webView];
        return YES;
    }
    return NO;
}

/// Chrome-style shortcuts while focus is inside the browser.
- (BOOL)performKeyEquivalent:(NSEvent *)event {
    NSResponder *responder = self.window.firstResponder;
    BOOL inside = [responder isKindOfClass:[NSView class]] && [(NSView *)responder isDescendantOf:self];
    if (!inside || self.hidden) return [super performKeyEquivalent:event];
    NSEventModifierFlags flags = event.modifierFlags & NSEventModifierFlagDeviceIndependentFlagsMask;
    NSString *key = event.charactersIgnoringModifiers.lowercaseString;
    BOOL cmd = flags == NSEventModifierFlagCommand;
    if (cmd && [key isEqual:@"t"]) { [self newTab]; return YES; }
    if (cmd && [key isEqual:@"w"]) { [self closeCurrentTab]; return YES; }
    if (cmd && [key isEqual:@"l"]) { [self focusAddressBar]; return YES; }
    if (cmd && [key isEqual:@"r"]) { [self.webView reload]; return YES; }
    if (cmd && [key isEqual:@"["]) { [self.webView goBack]; return YES; }
    if (cmd && [key isEqual:@"]"]) { [self.webView goForward]; return YES; }
    if (cmd && key.length == 1 && [key characterAtIndex:0] >= '1' && [key characterAtIndex:0] <= '9') {
        NSInteger n = [key characterAtIndex:0] - '1';
        [self selectIndex:n == 8 ? (NSInteger)self.pages.count - 1 : n];
        return YES;
    }
    if ((flags & NSEventModifierFlagControl) && event.keyCode == 48 && self.pages.count > 1) {  // ⌃Tab / ⌃⇧Tab
        NSInteger step = (flags & NSEventModifierFlagShift) ? -1 : 1;
        [self selectIndex:(self.current + step + (NSInteger)self.pages.count) % (NSInteger)self.pages.count];
        return YES;
    }
    return [super performKeyEquivalent:event];
}

#pragma mark Agent access

- (void)readPage:(WKWebView *)page attempt:(NSInteger)attempt completion:(void (^)(NSDictionary *))completion {
    if (!page.superview) {
        completion(@{@"error": @"That browser tab was closed."});
        return;
    }
    if ((page.loading || !page.URL) && attempt < 60) {
        __weak PipkinBrowserPane *weakSelf = self;
        dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(0.25 * NSEC_PER_SEC)), dispatch_get_main_queue(), ^{
            [weakSelf readPage:page attempt:attempt + 1 completion:completion];
        });
        return;
    }
    NSString *script = @"(() => { const main = document.querySelector('main, article, [role=main]') || document.body;"
                       @" const text = (main ? main.innerText : '') || (document.body ? document.body.innerText : '');"
                       @" return JSON.stringify({ title: document.title, url: location.href, text: text.slice(0, 40000) }); })()";
    [page evaluateJavaScript:script completionHandler:^(id value, NSError *error) {
        NSDictionary *result = nil;
        if ([value isKindOfClass:[NSString class]]) {
            result = [NSJSONSerialization JSONObjectWithData:[value dataUsingEncoding:NSUTF8StringEncoding] options:0 error:nil];
        }
        if (![result isKindOfClass:[NSDictionary class]]) {
            result = @{@"title": page.title ?: @"", @"url": page.URL.absoluteString ?: @"",
                       @"error": page.loading ? @"The page is still loading." : @"Could not read this page."};
        }
        completion(result);
    }];
}

- (void)agentRequest:(NSString *)op url:(NSURL *)url completion:(void (^)(NSDictionary *))completion {
    if ([op isEqual:@"open"]) {
        if (!url) {
            completion(@{@"error": @"Give a full http(s) URL."});
            return;
        }
        // Reuse a fresh home tab instead of leaving an empty Google tab behind.
        WKWebView *page = self.webView;
        BOOL fresh = page && !page.canGoBack && (!page.URL || [page.URL.absoluteString isEqual:kHome]);
        if (fresh) [self openURL:url];
        else [self openURLInNewTab:url];
        [self readPage:self.webView attempt:0 completion:completion];
    } else if (self.webView) {
        [self readPage:self.webView attempt:0 completion:completion];
    } else {
        completion(@{@"error": @"The browser has no open tab."});
    }
}

#pragma mark Web view

- (void)webView:(WKWebView *)webView decidePolicyForNavigationAction:(WKNavigationAction *)action decisionHandler:(void (^)(WKNavigationActionPolicy))decisionHandler {
    NSString *scheme = action.request.URL.scheme.lowercaseString ?: @"";
    if ([@[@"http", @"https", @"about", @"blob", @"data"] containsObject:scheme]) {
        // ⌘-click opens the link in a new background tab, like Chrome.
        if (action.navigationType == WKNavigationTypeLinkActivated && (action.modifierFlags & NSEventModifierFlagCommand) &&
            [@[@"http", @"https"] containsObject:scheme]) {
            [[self addTabWithConfiguration:nil select:NO] loadRequest:action.request];
            decisionHandler(WKNavigationActionPolicyCancel);
            return;
        }
        decisionHandler(WKNavigationActionPolicyAllow);
        return;
    }
    // mailto:, app links and the like go to macOS.
    if (action.navigationType == WKNavigationTypeLinkActivated && action.request.URL) {
        [[NSWorkspace sharedWorkspace] openURL:action.request.URL];
    }
    decisionHandler(WKNavigationActionPolicyCancel);
}

- (WKWebView *)webView:(WKWebView *)webView createWebViewWithConfiguration:(WKWebViewConfiguration *)configuration forNavigationAction:(WKNavigationAction *)action windowFeatures:(WKWindowFeatures *)features {
    // target=_blank links and pop-ups (sign-in windows included) open as a new tab that keeps its opener.
    return [self addTabWithConfiguration:configuration select:YES];
}

- (void)webViewDidClose:(WKWebView *)webView {
    [self closePage:webView];
}

- (void)webView:(WKWebView *)webView didFailProvisionalNavigation:(WKNavigation *)navigation withError:(NSError *)error {
    if (error.code == NSURLErrorCancelled) return;
    NSString *message = [error.localizedDescription stringByReplacingOccurrencesOfString:@"<" withString:@"&lt;"];
    NSString *html = [NSString stringWithFormat:
        @"<meta name=color-scheme content='light dark'><title>Can't open page</title><body style='font:14px -apple-system;padding:48px 28px;color:#888'>"
        @"<h3 style='color:CanvasText;font-weight:600'>Can't open this page</h3><p>%@</p></body>", message];
    [webView loadHTMLString:html baseURL:nil];
}

- (void)webView:(WKWebView *)webView runJavaScriptAlertPanelWithMessage:(NSString *)message initiatedByFrame:(WKFrameInfo *)frame completionHandler:(void (^)(void))completion {
    NSAlert *alert = [NSAlert new];
    alert.messageText = frame.request.URL.host ?: @"This page says";
    alert.informativeText = message;
    [alert beginSheetModalForWindow:self.window completionHandler:^(NSModalResponse r) { completion(); }];
}

- (void)webView:(WKWebView *)webView runJavaScriptConfirmPanelWithMessage:(NSString *)message initiatedByFrame:(WKFrameInfo *)frame completionHandler:(void (^)(BOOL))completion {
    NSAlert *alert = [NSAlert new];
    alert.messageText = frame.request.URL.host ?: @"This page asks";
    alert.informativeText = message;
    [alert addButtonWithTitle:@"OK"];
    [alert addButtonWithTitle:@"Cancel"];
    [alert beginSheetModalForWindow:self.window completionHandler:^(NSModalResponse r) { completion(r == NSAlertFirstButtonReturn); }];
}

- (void)webView:(WKWebView *)webView runOpenPanelWithParameters:(WKOpenPanelParameters *)parameters initiatedByFrame:(WKFrameInfo *)frame completionHandler:(void (^)(NSArray<NSURL *> *))completion {
    NSOpenPanel *panel = [NSOpenPanel openPanel];
    panel.allowsMultipleSelection = parameters.allowsMultipleSelection;
    panel.canChooseDirectories = parameters.allowsDirectories;
    [panel beginSheetModalForWindow:self.window completionHandler:^(NSModalResponse result) {
        completion(result == NSModalResponseOK ? panel.URLs : nil);
    }];
}

@end
