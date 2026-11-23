#import <Cocoa/Cocoa.h>
#import <WebKit/WebKit.h>

/// The built-in browser: a side pane with tabs, back/forward/reload, an address bar that also
/// searches Google, and WebKit web views. Links clicked in Pipkin open here unless Command is held.
@interface PipkinBrowserPane : NSView <WKNavigationDelegate, WKUIDelegate, NSTextFieldDelegate>
/// The selected tab's web view.
@property (strong, readonly) WKWebView *webView;
/// Called when the pane's close button is pressed.
@property (copy) void (^onClose)(void);
/// Opens url in the current tab (or a new one if there is none).
- (void)openURL:(NSURL *)url;
- (void)openURLInNewTab:(NSURL *)url;
- (void)openInput:(NSString *)text;
- (void)newTab;
- (void)closeCurrentTab;
/// Opens the home page if nothing has been loaded yet.
- (void)loadHomeIfEmpty;
- (void)focusAddressBar;
/// For the agent. op is "open" (new tab, then read once loaded) or "read" (current tab).
/// completion receives {title, url, text} or {error}.
- (void)agentRequest:(NSString *)op url:(NSURL *)url completion:(void (^)(NSDictionary *result))completion;
@end
