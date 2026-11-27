#import "PipkinBrowser.h"

static NSString *const kHome = @"https://www.google.com/";
static NSString *const kSearch = @"https://www.google.com/search";
static NSString *const kSafariUserAgent =
    @"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.2 Safari/605.1.15";
static NSArray<NSString *> *ObservedKeys(void) {
    return @[@"title", @"URL", @"canGoBack", @"canGoForward", @"loading", @"estimatedProgress"];
}

static BOOL IsDarkAppearance(NSAppearance *appearance) {
    return [[appearance bestMatchFromAppearancesWithNames:@[NSAppearanceNameAqua, NSAppearanceNameDarkAqua]] isEqual:NSAppearanceNameDarkAqua];
}

static NSColor *PipkinAccentColor(BOOL dark) {
    return dark
        ? [NSColor colorWithSRGBRed:0x3e / 255.0 green:0xd5 / 255.0 blue:0x98 / 255.0 alpha:1.0]
        : [NSColor colorWithSRGBRed:0x10 / 255.0 green:0x85 / 255.0 blue:0x5a / 255.0 alpha:1.0];
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
    _label.font = [NSFont systemFontOfSize:12 weight:NSFontWeightMedium];
    _label.lineBreakMode = NSLineBreakByTruncatingTail;
    _label.textColor = NSColor.secondaryLabelColor;
    [_label setContentCompressionResistancePriority:NSLayoutPriorityDefaultLow forOrientation:NSLayoutConstraintOrientationHorizontal];
    NSImageSymbolConfiguration *xCfg = [NSImageSymbolConfiguration configurationWithPointSize:9.5 weight:NSFontWeightSemibold];
    NSImage *xImg = [[NSImage imageWithSystemSymbolName:@"xmark" accessibilityDescription:@"Close tab"] imageWithSymbolConfiguration:xCfg];
    _close = [NSButton buttonWithImage:xImg target:self action:@selector(closeTab:)];
    _close.bordered = NO;
    _close.contentTintColor = NSColor.tertiaryLabelColor;
    _close.toolTip = @"Close tab (⌘W)";
    for (NSView *view in @[_label, _close]) {
        view.translatesAutoresizingMaskIntoConstraints = NO;
        [self addSubview:view];
    }
    [NSLayoutConstraint activateConstraints:@[
        [_label.leadingAnchor constraintEqualToAnchor:self.leadingAnchor constant:20],
        [_label.centerYAnchor constraintEqualToAnchor:self.centerYAnchor],
        [_close.leadingAnchor constraintEqualToAnchor:_label.trailingAnchor constant:6],
        [_close.trailingAnchor constraintEqualToAnchor:self.trailingAnchor constant:-8],
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
    self.close.contentTintColor = selected ? NSColor.secondaryLabelColor : NSColor.tertiaryLabelColor;
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
    BOOL dark = IsDarkAppearance(self.effectiveAppearance);
    NSRect box = NSInsetRect(self.bounds, 1, 3);
    if (self.selected || self.hovering) {
        NSColor *fill = self.selected
            ? (dark ? [NSColor colorWithSRGBRed:0x1b / 255.0 green:0x22 / 255.0 blue:0x20 / 255.0 alpha:0.98]
                    : [NSColor colorWithSRGBRed:1 green:1 blue:1 alpha:0.98])
            : (dark ? [NSColor colorWithSRGBRed:1 green:1 blue:1 alpha:0.05]
                    : [NSColor colorWithSRGBRed:0 green:0 blue:0 alpha:0.04]);
        [fill setFill];
        NSBezierPath *pill = [NSBezierPath bezierPathWithRoundedRect:box xRadius:9 yRadius:9];
        [pill fill];
        if (self.selected) {
            NSColor *border = dark
                ? [NSColor colorWithSRGBRed:1 green:1 blue:1 alpha:0.11]
                : [NSColor colorWithSRGBRed:0x0f / 255.0 green:0x1f / 255.0 blue:0x1a / 255.0 alpha:0.09];
            [border setStroke];
            pill.lineWidth = 1.0;
            [pill stroke];
        }
    }
    if (self.selected) {
        [PipkinAccentColor(dark) setFill];
        NSRect dot = NSMakeRect(NSMinX(box) + 9, NSMidY(box) - 3, 6, 6);
        [[NSBezierPath bezierPathWithOvalInRect:dot] fill];
    } else if (NSWidth(box) > 54) {
        [[NSColor.tertiaryLabelColor colorWithAlphaComponent:0.45] setFill];
        NSRect dot = NSMakeRect(NSMinX(box) + 10, NSMidY(box) - 2, 4, 4);
        [[NSBezierPath bezierPathWithOvalInRect:dot] fill];
    }
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
    CGFloat height = self.bounds.size.height, plus = 28, inset = 12, gap = 6;
    NSUInteger count = self.tabs.count;
    CGFloat available = self.bounds.size.width - plus - inset * 2 - (count > 0 ? gap * count : 0);
    CGFloat width = count ? MIN(210, MAX(56, available / count)) : 0;
    CGFloat x = inset;
    for (PipkinTabButton *tab in self.tabs) {
        tab.frame = NSMakeRect(x, 0, width, height);
        tab.label.hidden = width < 72;
        x += width + gap;
    }
    self.add.frame = NSMakeRect(MIN(x, self.bounds.size.width - plus - inset), (height - 26) / 2, 26, 26);
}

@end

#pragma mark - Chrome header & pill address bar

@interface PipkinChromeHeader : NSView
@end

@implementation PipkinChromeHeader
- (void)drawRect:(NSRect)dirtyRect {
    BOOL dark = IsDarkAppearance(self.effectiveAppearance);
    NSColor *bg = dark
        ? [NSColor colorWithSRGBRed:0x10 / 255.0 green:0x15 / 255.0 blue:0x14 / 255.0 alpha:1.0]
        : [NSColor colorWithSRGBRed:0xe7 / 255.0 green:0xef / 255.0 blue:0xec / 255.0 alpha:1.0];
    [bg setFill];
    NSRectFill(dirtyRect);
    NSColor *rule = dark
        ? [NSColor colorWithSRGBRed:1 green:1 blue:1 alpha:0.08]
        : [NSColor colorWithSRGBRed:0x0f / 255.0 green:0x1f / 255.0 blue:0x1a / 255.0 alpha:0.09];
    [rule setFill];
    NSRectFill(NSMakeRect(0, 0, NSWidth(self.bounds), 1));
}
@end

@interface PipkinAddressPill : NSView
@property (assign, nonatomic) BOOL focused;
@end

@implementation PipkinAddressPill
- (void)setFocused:(BOOL)focused {
    if (_focused == focused) return;
    _focused = focused;
    self.needsDisplay = YES;
}
- (void)drawRect:(NSRect)dirtyRect {
    BOOL dark = IsDarkAppearance(self.effectiveAppearance);
    NSRect box = NSInsetRect(self.bounds, 1, 1);
    NSColor *fill = dark
        ? [NSColor colorWithSRGBRed:0x16 / 255.0 green:0x1c / 255.0 blue:0x1a / 255.0 alpha:1.0]
        : [NSColor colorWithSRGBRed:1 green:1 blue:1 alpha:0.96];
    [fill setFill];
    NSBezierPath *pill = [NSBezierPath bezierPathWithRoundedRect:box xRadius:10 yRadius:10];
    [pill fill];
    NSColor *border = self.focused
        ? [PipkinAccentColor(dark) colorWithAlphaComponent:0.85]
        : (dark ? [NSColor colorWithSRGBRed:1 green:1 blue:1 alpha:0.12]
                : [NSColor colorWithSRGBRed:0x0f / 255.0 green:0x1f / 255.0 blue:0x1a / 255.0 alpha:0.11]);
    [border setStroke];
    pill.lineWidth = self.focused ? 1.5 : 1.0;
    [pill stroke];
}
@end

#pragma mark - Pane

@interface PipkinBrowserPane ()
@property (strong) NSMutableArray<WKWebView *> *pages;
@property (strong) PipkinTabStrip *strip;
@property (strong) PipkinAddressPill *addressPill;
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
    NSImageSymbolConfiguration *cfg = [NSImageSymbolConfiguration configurationWithPointSize:13.5 weight:NSFontWeightMedium];
    NSImage *image = [[NSImage imageWithSystemSymbolName:symbol accessibilityDescription:tip] imageWithSymbolConfiguration:cfg];
    NSButton *button = [NSButton buttonWithImage:image target:target action:action];
    button.bezelStyle = NSBezelStyleTexturedRounded;
    button.bordered = NO;
    button.toolTip = tip;
    button.contentTintColor = NSColor.secondaryLabelColor;
    button.translatesAutoresizingMaskIntoConstraints = NO;
    [button.widthAnchor constraintEqualToConstant:30].active = YES;
    [button.heightAnchor constraintEqualToConstant:28].active = YES;
    return button;
}

- (instancetype)initWithFrame:(NSRect)frame {
    self = [super initWithFrame:frame];
    if (!self) return nil;
    _pages = [NSMutableArray array];
    _current = -1;

    PipkinChromeHeader *chrome = [PipkinChromeHeader new];
    chrome.translatesAutoresizingMaskIntoConstraints = NO;

    _strip = [[PipkinTabStrip alloc] initWithFrame:NSZeroRect];
    _strip.translatesAutoresizingMaskIntoConstraints = NO;
    NSImageSymbolConfiguration *plusCfg = [NSImageSymbolConfiguration configurationWithPointSize:13 weight:NSFontWeightMedium];
    NSImage *plusImg = [[NSImage imageWithSystemSymbolName:@"plus" accessibilityDescription:@"New tab"] imageWithSymbolConfiguration:plusCfg];
    _strip.add = [NSButton buttonWithImage:plusImg target:self action:@selector(newTab)];
    _strip.add.bordered = NO;
    _strip.add.toolTip = @"New tab (⌘T)";
    _strip.add.contentTintColor = NSColor.secondaryLabelColor;
    [_strip addSubview:_strip.add];

    _back = ToolButton(@"chevron.left", @"Back (⌘[)", self, @selector(goBack:));
    _forward = ToolButton(@"chevron.right", @"Forward (⌘])", self, @selector(goForward:));
    _reload = ToolButton(@"arrow.clockwise", @"Reload (⌘R)", self, @selector(reloadOrStop:));
    NSButton *external = ToolButton(@"safari", @"Open in your default browser", self, @selector(openExternally:));
    NSButton *close = ToolButton(@"sidebar.right", @"Hide browser (⌘⇧B)", self, @selector(closePane:));

    _addressPill = [PipkinAddressPill new];
    _addressPill.translatesAutoresizingMaskIntoConstraints = NO;

    NSImageSymbolConfiguration *searchCfg = [NSImageSymbolConfiguration configurationWithPointSize:12 weight:NSFontWeightMedium];
    NSImageView *searchIcon = [NSImageView imageViewWithImage:[[NSImage imageWithSystemSymbolName:@"magnifyingglass" accessibilityDescription:nil] imageWithSymbolConfiguration:searchCfg]];
    searchIcon.contentTintColor = NSColor.tertiaryLabelColor;
    searchIcon.translatesAutoresizingMaskIntoConstraints = NO;

    _address = [NSTextField new];
    _address.placeholderString = @"Search Google or enter a URL";
    _address.bordered = NO;
    _address.drawsBackground = NO;
    _address.focusRingType = NSFocusRingTypeNone;
    _address.font = [NSFont systemFontOfSize:12.5];
    _address.lineBreakMode = NSLineBreakByTruncatingTail;
    _address.cell.scrollable = YES;
    _address.delegate = self;
    _address.target = self;
    _address.action = @selector(addressEntered:);
    _address.translatesAutoresizingMaskIntoConstraints = NO;

    [_addressPill addSubview:searchIcon];
    [_addressPill addSubview:_address];
    [NSLayoutConstraint activateConstraints:@[
        [_addressPill.heightAnchor constraintEqualToConstant:32],
        [searchIcon.leadingAnchor constraintEqualToAnchor:_addressPill.leadingAnchor constant:11],
        [searchIcon.centerYAnchor constraintEqualToAnchor:_addressPill.centerYAnchor],
        [searchIcon.widthAnchor constraintEqualToConstant:14],
        [searchIcon.heightAnchor constraintEqualToConstant:14],
        [_address.leadingAnchor constraintEqualToAnchor:searchIcon.trailingAnchor constant:7],
        [_address.trailingAnchor constraintEqualToAnchor:_addressPill.trailingAnchor constant:-10],
        [_address.centerYAnchor constraintEqualToAnchor:_addressPill.centerYAnchor],
    ]];

    _progress = [NSProgressIndicator new];
    _progress.style = NSProgressIndicatorStyleBar;
    _progress.indeterminate = NO;
    _progress.minValue = 0;
    _progress.maxValue = 1;
    _progress.controlSize = NSControlSizeSmall;
    _progress.hidden = YES;
    _progress.translatesAutoresizingMaskIntoConstraints = NO;

    NSStackView *navGroup = [NSStackView stackViewWithViews:@[_back, _forward, _reload]];
    navGroup.orientation = NSUserInterfaceLayoutOrientationHorizontal;
    navGroup.spacing = 2;

    NSStackView *rightGroup = [NSStackView stackViewWithViews:@[external, close]];
    rightGroup.orientation = NSUserInterfaceLayoutOrientationHorizontal;
    rightGroup.spacing = 4;

    NSStackView *bar = [NSStackView stackViewWithViews:@[navGroup, _addressPill, rightGroup]];
    bar.orientation = NSUserInterfaceLayoutOrientationHorizontal;
    bar.alignment = NSLayoutAttributeCenterY;
    bar.spacing = 10;
    bar.edgeInsets = NSEdgeInsetsMake(4, 10, 10, 10);
    bar.translatesAutoresizingMaskIntoConstraints = NO;
    [_addressPill setContentHuggingPriority:NSLayoutPriorityDefaultLow forOrientation:NSLayoutConstraintOrientationHorizontal];

    [chrome addSubview:_strip];
    [chrome addSubview:bar];

    _content = [NSView new];
    _content.translatesAutoresizingMaskIntoConstraints = NO;

    for (NSView *view in @[chrome, _content, _progress]) [self addSubview:view];
    [NSLayoutConstraint activateConstraints:@[
        [chrome.topAnchor constraintEqualToAnchor:self.topAnchor],
        [chrome.leadingAnchor constraintEqualToAnchor:self.leadingAnchor],
        [chrome.trailingAnchor constraintEqualToAnchor:self.trailingAnchor],
        [_strip.topAnchor constraintEqualToAnchor:chrome.topAnchor constant:6],
        [_strip.leadingAnchor constraintEqualToAnchor:chrome.leadingAnchor],
        [_strip.trailingAnchor constraintEqualToAnchor:chrome.trailingAnchor],
        [_strip.heightAnchor constraintEqualToConstant:36],
        [bar.topAnchor constraintEqualToAnchor:_strip.bottomAnchor constant:2],
        [bar.leadingAnchor constraintEqualToAnchor:chrome.leadingAnchor],
        [bar.trailingAnchor constraintEqualToAnchor:chrome.trailingAnchor],
        [bar.bottomAnchor constraintEqualToAnchor:chrome.bottomAnchor],
        [_progress.topAnchor constraintEqualToAnchor:chrome.bottomAnchor constant:-1],
        [_progress.leadingAnchor constraintEqualToAnchor:self.leadingAnchor],
        [_progress.trailingAnchor constraintEqualToAnchor:self.trailingAnchor],
        [_progress.heightAnchor constraintEqualToConstant:3],
        [_content.topAnchor constraintEqualToAnchor:chrome.bottomAnchor],
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
        config.applicationNameForUserAgent = @"Version/18.2 Safari/605.1.15";
        config.defaultWebpagePreferences.preferredContentMode = WKContentModeDesktop;
        config.defaultWebpagePreferences.allowsContentJavaScript = YES;
        config.preferences.javaScriptCanOpenWindowsAutomatically = YES;
        @try { [config.preferences setValue:@YES forKey:@"developerExtrasEnabled"]; } @catch (NSException *e) {}
    }
    WKWebView *page = [[WKWebView alloc] initWithFrame:NSZeroRect configuration:config];
    page.customUserAgent = kSafariUserAgent;
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

- (void)openInput:(NSString *)text {
    NSString *trimmed = [text stringByTrimmingCharactersInSet:NSCharacterSet.whitespaceAndNewlineCharacterSet];
    if (!trimmed.length) {
        [self loadHomeIfEmpty];
        [self focusAddressBar];
        return;
    }
    [self openURL:[self urlForInput:trimmed]];
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
    NSImageSymbolConfiguration *cfg = [NSImageSymbolConfiguration configurationWithPointSize:13.5 weight:NSFontWeightMedium];
    self.reload.image = [[NSImage imageWithSystemSymbolName:loading ? @"xmark" : @"arrow.clockwise" accessibilityDescription:loading ? @"Stop" : @"Reload"] imageWithSymbolConfiguration:cfg];
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
    NSURL *url = self.webView.URL;
    NSString *scheme = url.scheme.lowercaseString ?: @"";
    if (url && [@[@"http", @"https"] containsObject:scheme]) {
        [[NSWorkspace sharedWorkspace] openURL:url];
    }
}
- (void)closePane:(id)sender { if (self.onClose) self.onClose(); }

/// Turns what was typed into a URL: an address if it looks like one, otherwise a Google search.
- (NSURL *)urlForInput:(NSString *)text {
    NSURLComponents *parts = [NSURLComponents componentsWithString:text];
    NSString *scheme = parts.scheme.lowercaseString ?: @"";
    BOOL hasScheme = [@[@"http", @"https"] containsObject:scheme];
    BOOL looksLikeHost = !scheme.length && ![text containsString:@" "] && ([text containsString:@"."] || [text hasPrefix:@"localhost"]);
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

- (void)controlTextDidBeginEditing:(NSNotification *)notification {
    self.addressPill.focused = YES;
}

- (void)controlTextDidEndEditing:(NSNotification *)notification {
    self.addressPill.focused = NO;
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
    // Only forward safe external link schemes (mailto/tel) to macOS; never file:// or smb://.
    if (action.navigationType == WKNavigationTypeLinkActivated && action.request.URL &&
        [@[@"mailto", @"tel"] containsObject:scheme]) {
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
        @"<meta name=color-scheme content='light dark'><title>Can't open page</title><style>"
        @"body{margin:0;min-height:100vh;display:grid;place-items:center;padding:32px;font:14px -apple-system,sans-serif;background:#edf3f1;color:#36453f}"
        @"@media(prefers-color-scheme:dark){body{background:#0c0f0e;color:#c2cbc6}}"
        @"main{max-width:400px;text-align:center}h3{margin:0 0 8px;font-size:16px;font-weight:600;letter-spacing:-.02em}p{margin:0;opacity:.78;line-height:1.5}"
        @"</style><main><h3>Can't open this page</h3><p>%@</p></main>", message];
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

// Websites opened in Pipkin's browser never get the microphone or camera.
- (void)webView:(WKWebView *)webView requestMediaCapturePermissionForOrigin:(WKSecurityOrigin *)origin initiatedByFrame:(WKFrameInfo *)frame type:(WKMediaCaptureType)type decisionHandler:(void (^)(WKPermissionDecision))decisionHandler API_AVAILABLE(macos(12.0)) {
    decisionHandler(WKPermissionDecisionDeny);
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
