#import <Cocoa/Cocoa.h>

/// Sprig: Pipkin's floating desktop companion. A small animated character that sits on the
/// desktop, follows the cursor with its eyes, reacts to what Pipkin is doing, and offers a
/// hover pill (New chat, Ask, Agent). Drag to move; click to open Pipkin; right-click for more.
///
/// The action block receives: "open", "new", "chat", "work", "skills", "hide", or "ask:<text>".
@interface PipkinPet : NSObject
@property (nonatomic, assign, getter=isVisible) BOOL visible;
/// YES while Pipkin is tucked away (server stopped): Sprig sleeps.
@property (nonatomic, assign, getter=isDocked) BOOL docked;
@property (nonatomic, readonly, copy) NSString *mood;
- (instancetype)initWithImage:(NSImage *)image action:(void (^)(NSString *action))action;
/// "idle", "thinking", "attention" or "happy".
- (void)setMood:(NSString *)mood;
/// Shows a short speech bubble above Sprig for a few seconds. Clicking it opens Pipkin.
- (void)say:(NSString *)text;
@end
