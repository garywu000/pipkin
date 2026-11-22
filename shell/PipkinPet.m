#import "PipkinPet.h"
#import <QuartzCore/QuartzCore.h>

// Sprig is drawn with Core Animation layers so it can breathe, blink, sway, look around and react.
// Layer coordinates are bottom-left based; the character canvas is 96 × 104 points.

static CGColorRef Hex(unsigned hex, CGFloat alpha) {
    return [NSColor colorWithSRGBRed:((hex >> 16) & 0xff) / 255.0 green:((hex >> 8) & 0xff) / 255.0
                                blue:(hex & 0xff) / 255.0 alpha:alpha].CGColor;
}

static CAShapeLayer *Shape(CGPathRef path, CGColorRef fill) {
    CAShapeLayer *layer = [CAShapeLayer layer];
    layer.path = path;
    layer.fillColor = fill;
    CGPathRelease(path);
    return layer;
}

static CGPathRef Ellipse(CGRect rect) { return CGPathCreateWithEllipseInRect(rect, NULL); }

// Character canvas height: the drawing is 104 pt tall; the rest is headroom so hops and sprouts are never clipped.
static const CGFloat kCharHeight = 126;

static BOOL ReduceMotion(void) { return NSWorkspace.sharedWorkspace.accessibilityDisplayShouldReduceMotion; }

#pragma mark - Panel

@interface SprigPanel : NSPanel
@end

@implementation SprigPanel
- (BOOL)canBecomeKeyWindow { return YES; }   // lets the quick-ask field take typing without activating Pipkin
- (BOOL)canBecomeMainWindow { return NO; }
@end

@interface PipkinPet ()
- (void)sprigClicked;
- (void)sprigDragFrom:(NSPoint)startAnchor delta:(NSPoint)delta;
- (void)sprigDragEnded;
- (void)showMenu:(NSEvent *)event inView:(NSView *)view;
- (void)hoverChanged:(BOOL)inside;
- (void)bubbleClicked;
@end

#pragma mark - Character

@interface SprigView : NSView
@property (nonatomic, weak) PipkinPet *owner;
@property (nonatomic, strong) CALayer *body, *leftEye, *rightEye, *leftPupil, *rightPupil, *leftSprout, *rightSprout;
@property (nonatomic, strong) CAShapeLayer *mouth, *groundShadow;
@property (nonatomic, strong) CALayer *thinking, *alert, *sleep;
@property (nonatomic, assign) NSPoint downLocation, startAnchor;
@property (nonatomic, assign) BOOL dragging, sleeping;
@property (nonatomic, copy) NSString *mood;
@end

@implementation SprigView

- (CALayer *)eyeAt:(CGPoint)center pupil:(CALayer **)pupilOut {
    CALayer *eye = [CALayer layer];
    eye.bounds = CGRectMake(0, 0, 22, 22);
    eye.position = center;
    [eye addSublayer:Shape(Ellipse(CGRectMake(1, 1, 20, 20)), Hex(0xFFFFFF, 1))];
    CALayer *pupil = [CALayer layer];
    pupil.bounds = CGRectMake(0, 0, 11, 11);
    pupil.position = CGPointMake(11, 11);
    [pupil addSublayer:Shape(Ellipse(CGRectMake(0, 0, 11, 11)), Hex(0x1F2B25, 1))];
    [pupil addSublayer:Shape(Ellipse(CGRectMake(6, 6.2, 3.4, 3.4)), Hex(0xFFFFFF, 0.95))];
    [eye addSublayer:pupil];
    *pupilOut = pupil;
    return eye;
}

- (CALayer *)sproutFrom:(CGPoint)base to:(CGPoint)tip {
    CALayer *group = [CALayer layer];
    group.bounds = CGRectMake(0, 0, 96, 104);
    group.anchorPoint = CGPointMake(base.x / 96, base.y / 104);
    group.position = base;
    CGPoint control = CGPointMake(base.x + (tip.x - base.x) * 0.15, (base.y + tip.y) / 2 + 2);
    CGMutablePathRef stem = CGPathCreateMutable();
    CGPathMoveToPoint(stem, NULL, base.x, base.y);
    CGPathAddQuadCurveToPoint(stem, NULL, control.x, control.y, tip.x, tip.y);
    CAShapeLayer *stemLayer = Shape(stem, NULL);
    stemLayer.strokeColor = Hex(0x47904E, 1);
    stemLayer.lineWidth = 2.2;
    stemLayer.lineCap = kCALineCapRound;
    [group addSublayer:stemLayer];
    // Fern leaflets along the stem, alternating sides, getting smaller toward the tip.
    const CGFloat stops[] = {0.42, 0.58, 0.72, 0.85, 1.0};
    for (int index = 0; index < 5; index++) {
        CGFloat t = stops[index], u = 1 - t;
        CGPoint point = CGPointMake(u * u * base.x + 2 * u * t * control.x + t * t * tip.x,
                                    u * u * base.y + 2 * u * t * control.y + t * t * tip.y);
        CGPoint tangent = CGPointMake(2 * u * (control.x - base.x) + 2 * t * (tip.x - control.x),
                                      2 * u * (control.y - base.y) + 2 * t * (tip.y - control.y));
        CGFloat angle = atan2(tangent.y, tangent.x);
        BOOL last = index == 4;
        CGFloat side = last ? 0 : (index % 2 ? 1 : -1);
        CGFloat length = last ? 11 : 10 - index * 1.2, width = last ? 6.5 : 5.2 - index * 0.5;
        CAShapeLayer *leaf = Shape(Ellipse(CGRectMake(0, -width / 2, length, width)), Hex(index % 2 ? 0x7CC56F : 0x5EAE5E, 1));
        leaf.position = point;
        leaf.affineTransform = CGAffineTransformMakeRotation(angle + side * 0.95);
        [group addSublayer:leaf];
    }
    return group;
}

- (instancetype)initWithFrame:(NSRect)frame {
    self = [super initWithFrame:frame];
    if (!self) return nil;
    CALayer *root = [CALayer layer];
    self.layer = root;
    self.wantsLayer = YES;

    _groundShadow = Shape(Ellipse(CGRectMake(22, 2, 52, 9)), Hex(0x000000, 0.16));
    [root addSublayer:_groundShadow];

    _body = [CALayer layer];
    _body.bounds = CGRectMake(0, 0, 96, 104);
    _body.anchorPoint = CGPointMake(0.5, 0);
    _body.position = CGPointMake(48, 5);
    [root addSublayer:_body];

    _leftSprout = [self sproutFrom:CGPointMake(46, 58) to:CGPointMake(33, 92)];
    _rightSprout = [self sproutFrom:CGPointMake(50, 58) to:CGPointMake(63, 94)];
    [_body addSublayer:_leftSprout];
    [_body addSublayer:_rightSprout];

    [_body addSublayer:Shape(Ellipse(CGRectMake(18, 0, 22, 11)), Hex(0x4C9653, 1))];
    [_body addSublayer:Shape(Ellipse(CGRectMake(56, 0, 22, 11)), Hex(0x4C9653, 1))];

    CAGradientLayer *torso = [CAGradientLayer layer];
    torso.frame = CGRectMake(14, 4, 68, 58);
    torso.colors = @[(__bridge id)Hex(0x5BA75F, 1), (__bridge id)Hex(0x96D585, 1)];
    torso.startPoint = CGPointMake(0.5, 0);
    torso.endPoint = CGPointMake(0.5, 1);
    CAShapeLayer *torsoMask = [CAShapeLayer layer];
    CGPathRef torsoPath = CGPathCreateWithRoundedRect(CGRectMake(0, 0, 68, 58), 29, 28, NULL);
    torsoMask.path = torsoPath;
    CGPathRelease(torsoPath);
    torso.mask = torsoMask;
    [_body addSublayer:torso];

    [_body addSublayer:Shape(Ellipse(CGRectMake(31, 6, 34, 29)), Hex(0xEFF6DA, 1))];
    [_body addSublayer:Shape(Ellipse(CGRectMake(21.5, 46.5, 27, 27)), Hex(0x8BCF7C, 1))];
    [_body addSublayer:Shape(Ellipse(CGRectMake(47.5, 46.5, 27, 27)), Hex(0x8BCF7C, 1))];
    CALayer *pupil;
    _leftEye = [self eyeAt:CGPointMake(35, 60) pupil:&pupil];
    _leftPupil = pupil;
    _rightEye = [self eyeAt:CGPointMake(61, 60) pupil:&pupil];
    _rightPupil = pupil;
    [_body addSublayer:_leftEye];
    [_body addSublayer:_rightEye];
    [_body addSublayer:Shape(Ellipse(CGRectMake(20, 37, 12, 7)), Hex(0xF29A9A, 0.55))];
    [_body addSublayer:Shape(Ellipse(CGRectMake(64, 37, 12, 7)), Hex(0xF29A9A, 0.55))];

    _mouth = [CAShapeLayer layer];
    _mouth.strokeColor = Hex(0x2C5634, 1);
    _mouth.lineWidth = 2.2;
    _mouth.lineCap = kCALineCapRound;
    _mouth.fillColor = NULL;
    [_body addSublayer:_mouth];

    // Mood decorations
    _thinking = [CALayer layer];
    _thinking.frame = CGRectMake(64, 82, 30, 17);
    _thinking.backgroundColor = Hex(0xFFFFFF, 0.96);
    _thinking.cornerRadius = 8.5;
    _thinking.borderColor = Hex(0x000000, 0.08);
    _thinking.borderWidth = 0.5;
    for (int index = 0; index < 3; index++) {
        CAShapeLayer *dot = Shape(Ellipse(CGRectMake(6.5 + index * 7, 6.5, 4.2, 4.2)), Hex(0x5C6B62, 1));
        CABasicAnimation *pulse = [CABasicAnimation animationWithKeyPath:@"opacity"];
        pulse.fromValue = @0.25;
        pulse.toValue = @1;
        pulse.duration = 0.5;
        pulse.autoreverses = YES;
        pulse.repeatCount = HUGE_VALF;
        pulse.beginTime = CACurrentMediaTime() + index * 0.17;
        [dot addAnimation:pulse forKey:@"pulse"];
        [_thinking addSublayer:dot];
    }
    _thinking.hidden = YES;
    [root addSublayer:_thinking];

    CATextLayer *alert = [CATextLayer layer];
    alert.string = @"!";
    alert.font = (__bridge CFTypeRef)[NSFont systemFontOfSize:20 weight:NSFontWeightHeavy];
    alert.fontSize = 20;
    alert.foregroundColor = Hex(0xE0782C, 1);
    alert.alignmentMode = kCAAlignmentCenter;
    alert.frame = CGRectMake(68, 76, 18, 26);
    alert.contentsScale = 2;
    alert.hidden = YES;
    _alert = alert;
    [root addSublayer:alert];

    _sleep = [CALayer layer];
    _sleep.frame = CGRectMake(62, 64, 34, 40);
    for (int index = 0; index < 2; index++) {
        CATextLayer *z = [CATextLayer layer];
        z.string = @"z";
        z.fontSize = index ? 10 : 13;
        z.font = (__bridge CFTypeRef)[NSFont systemFontOfSize:13 weight:NSFontWeightBold];
        z.foregroundColor = Hex(0x6E9A75, 1);
        z.frame = CGRectMake(index * 10, index * 8, 14, 16);
        z.contentsScale = 2;
        CABasicAnimation *rise = [CABasicAnimation animationWithKeyPath:@"transform.translation.y"];
        rise.fromValue = @0;
        rise.toValue = @12;
        CABasicAnimation *fade = [CABasicAnimation animationWithKeyPath:@"opacity"];
        fade.fromValue = @1;
        fade.toValue = @0;
        CAAnimationGroup *drift = [CAAnimationGroup animation];
        drift.animations = @[rise, fade];
        drift.duration = 2.2;
        drift.repeatCount = HUGE_VALF;
        drift.beginTime = CACurrentMediaTime() + index * 1.1;
        [z addAnimation:drift forKey:@"drift"];
        [_sleep addSublayer:z];
    }
    _sleep.hidden = YES;
    [root addSublayer:_sleep];

    _mood = @"idle";
    [self applyMood];
    return self;
}

- (BOOL)acceptsFirstMouse:(NSEvent *)event { return YES; }
- (BOOL)mouseDownCanMoveWindow { return NO; }

- (NSView *)hitTest:(NSPoint)point {
    NSPoint local = [self convertPoint:point fromView:self.superview];
    return NSPointInRect(local, NSInsetRect(self.bounds, 10, 2)) ? self : nil;
}

- (void)resetCursorRects { [self addCursorRect:NSInsetRect(self.bounds, 10, 2) cursor:NSCursor.openHandCursor]; }

- (void)mouseDown:(NSEvent *)event {
    self.downLocation = NSEvent.mouseLocation;
    self.dragging = NO;
    [self.owner sprigDragFrom:NSZeroPoint delta:NSZeroPoint];
}

- (void)mouseDragged:(NSEvent *)event {
    NSPoint now = NSEvent.mouseLocation;
    NSPoint delta = NSMakePoint(now.x - self.downLocation.x, now.y - self.downLocation.y);
    if (!self.dragging && hypot(delta.x, delta.y) < 3) return;
    if (!self.dragging) [NSCursor.closedHandCursor push];
    self.dragging = YES;
    [self.owner sprigDragFrom:self.startAnchor delta:delta];
}

- (void)mouseUp:(NSEvent *)event {
    if (self.dragging) {
        [NSCursor pop];
        self.dragging = NO;
        [self.owner sprigDragEnded];
    } else {
        [self hop];
        [self.owner sprigClicked];
    }
}

- (void)rightMouseDown:(NSEvent *)event { [self.owner showMenu:event inView:self]; }

#pragma mark Animation

- (void)setPath:(CGPathRef)path onMouth:(BOOL)filled {
    self.mouth.path = path;
    self.mouth.fillColor = filled ? Hex(0x2C5634, 1) : NULL;
    CGPathRelease(path);
}

- (void)applyMood {
    [CATransaction begin];
    [CATransaction setDisableActions:YES];
    BOOL sleeping = self.sleeping;
    NSString *mood = sleeping ? @"sleeping" : self.mood;
    self.thinking.hidden = ![mood isEqual:@"thinking"];
    self.alert.hidden = ![mood isEqual:@"attention"];
    self.sleep.hidden = !sleeping;
    CGMutablePathRef mouth = CGPathCreateMutable();
    if ([mood isEqual:@"attention"]) {
        [self setPath:Ellipse(CGRectMake(45, 37, 6, 7)) onMouth:YES];
        CGPathRelease(mouth);
    } else {
        if ([mood isEqual:@"happy"]) {
            CGPathMoveToPoint(mouth, NULL, 39, 45);
            CGPathAddQuadCurveToPoint(mouth, NULL, 48, 34, 57, 45);
        } else if (sleeping) {
            CGPathMoveToPoint(mouth, NULL, 45, 42);
            CGPathAddLineToPoint(mouth, NULL, 51, 42);
        } else {
            CGPathMoveToPoint(mouth, NULL, 42, 44);
            CGPathAddQuadCurveToPoint(mouth, NULL, 48, 38.5, 54, 44);
        }
        [self setPath:mouth onMouth:NO];
    }
    CATransform3D closed = CATransform3DMakeScale(1, 0.1, 1);
    self.leftEye.transform = sleeping ? closed : CATransform3DIdentity;
    self.rightEye.transform = sleeping ? closed : CATransform3DIdentity;
    [CATransaction commit];

    // Breathing and sprout sway, paced by mood.
    [self.body removeAnimationForKey:@"breathe"];
    [self.leftSprout removeAnimationForKey:@"sway"];
    [self.rightSprout removeAnimationForKey:@"sway"];
    self.leftSprout.affineTransform = sleeping ? CGAffineTransformMakeRotation(0.28) : CGAffineTransformIdentity;
    self.rightSprout.affineTransform = sleeping ? CGAffineTransformMakeRotation(-0.28) : CGAffineTransformIdentity;
    if (ReduceMotion()) return;
    CABasicAnimation *breathe = [CABasicAnimation animationWithKeyPath:@"transform.scale.y"];
    breathe.fromValue = @1;
    breathe.toValue = @(sleeping ? 1.035 : 1.025);
    breathe.duration = sleeping ? 2.6 : 1.7;
    breathe.autoreverses = YES;
    breathe.repeatCount = HUGE_VALF;
    breathe.timingFunction = [CAMediaTimingFunction functionWithName:kCAMediaTimingFunctionEaseInEaseOut];
    [self.body addAnimation:breathe forKey:@"breathe"];
    if (sleeping) return;
    CGFloat pace = [mood isEqual:@"thinking"] ? 0.45 : 1;
    NSArray *sprouts = @[self.leftSprout, self.rightSprout];
    for (NSUInteger index = 0; index < 2; index++) {
        CABasicAnimation *sway = [CABasicAnimation animationWithKeyPath:@"transform.rotation.z"];
        sway.fromValue = @(index ? 0.07 : -0.06);
        sway.toValue = @(index ? -0.06 : 0.08);
        sway.duration = (index ? 2.9 : 2.4) * pace;
        sway.autoreverses = YES;
        sway.repeatCount = HUGE_VALF;
        sway.timingFunction = [CAMediaTimingFunction functionWithName:kCAMediaTimingFunctionEaseInEaseOut];
        [sprouts[index] addAnimation:sway forKey:@"sway"];
    }
}

- (void)blink {
    if (self.sleeping) return;
    CAKeyframeAnimation *blink = [CAKeyframeAnimation animationWithKeyPath:@"transform.scale.y"];
    blink.values = @[@1, @0.08, @1];
    blink.keyTimes = @[@0, @0.45, @1];
    blink.duration = 0.17;
    [self.leftEye addAnimation:blink forKey:@"blink"];
    [self.rightEye addAnimation:blink forKey:@"blink"];
}

- (void)hop {
    if (ReduceMotion()) return;
    CAKeyframeAnimation *hop = [CAKeyframeAnimation animationWithKeyPath:@"transform.translation.y"];
    hop.values = @[@0, @15, @0, @5, @0];
    hop.keyTimes = @[@0, @0.32, @0.58, @0.78, @1];
    hop.duration = 0.62;
    hop.timingFunctions = @[[CAMediaTimingFunction functionWithName:kCAMediaTimingFunctionEaseOut],
                            [CAMediaTimingFunction functionWithName:kCAMediaTimingFunctionEaseIn],
                            [CAMediaTimingFunction functionWithName:kCAMediaTimingFunctionEaseOut],
                            [CAMediaTimingFunction functionWithName:kCAMediaTimingFunctionEaseIn]];
    [self.body addAnimation:hop forKey:@"hop"];
    CAKeyframeAnimation *squash = [CAKeyframeAnimation animationWithKeyPath:@"transform.scale.x"];
    squash.values = @[@1, @0.8, @1, @0.92, @1];
    squash.keyTimes = hop.keyTimes;
    squash.duration = hop.duration;
    [self.groundShadow addAnimation:squash forKey:@"squash"];
}

- (void)lookAt:(NSPoint)screenPoint {
    if (self.sleeping || !self.window) return;
    CGPoint offsets[2];
    NSArray *eyes = @[self.leftEye, self.rightEye];
    for (NSUInteger index = 0; index < 2; index++) {
        CALayer *eye = eyes[index];
        NSPoint local = NSMakePoint(eye.position.x, eye.position.y + 5);
        NSPoint screen = [self.window convertPointToScreen:[self convertPoint:local toView:nil]];
        CGFloat dx = screenPoint.x - screen.x, dy = screenPoint.y - screen.y, distance = hypot(dx, dy);
        CGFloat reach = distance < 1 ? 0 : MIN(3.6, distance / 28) / distance;
        offsets[index] = [self.mood isEqual:@"thinking"] ? CGPointMake(2.4, 2.6) : CGPointMake(dx * reach, dy * reach);
    }
    [CATransaction begin];
    [CATransaction setAnimationDuration:0.12];
    self.leftPupil.position = CGPointMake(11 + offsets[0].x, 11 + offsets[0].y);
    self.rightPupil.position = CGPointMake(11 + offsets[1].x, 11 + offsets[1].y);
    [CATransaction commit];
}

@end

#pragma mark - Hover tracking and bubble

@interface SprigRootView : NSView
@property (nonatomic, weak) PipkinPet *owner;
@property (nonatomic, strong) NSTrackingArea *area;
@end

@implementation SprigRootView
- (void)updateTrackingAreas {
    [super updateTrackingAreas];
    if (self.area) [self removeTrackingArea:self.area];
    self.area = [[NSTrackingArea alloc] initWithRect:NSZeroRect
        options:NSTrackingMouseEnteredAndExited | NSTrackingActiveAlways | NSTrackingInVisibleRect owner:self userInfo:nil];
    [self addTrackingArea:self.area];
}
- (void)mouseEntered:(NSEvent *)event { [self.owner hoverChanged:YES]; }
- (void)mouseExited:(NSEvent *)event { [self.owner hoverChanged:NO]; }
- (NSView *)hitTest:(NSPoint)point {
    NSView *hit = [super hitTest:point];
    return hit == self ? nil : hit;   // empty space around Sprig never swallows clicks
}
@end

@interface SprigBubble : NSVisualEffectView
@property (nonatomic, weak) PipkinPet *owner;
@end

@implementation SprigBubble
- (BOOL)acceptsFirstMouse:(NSEvent *)event { return YES; }
- (void)mouseDown:(NSEvent *)event { [self.owner bubbleClicked]; }
@end

#pragma mark - Pet

@interface PipkinPet () <NSTextFieldDelegate>
@property (nonatomic, strong) SprigPanel *panel;
@property (nonatomic, strong) SprigRootView *root;
@property (nonatomic, strong) SprigView *sprig;
@property (nonatomic, strong) NSVisualEffectView *pill;
@property (nonatomic, strong) NSView *buttonRow, *askRow;
@property (nonatomic, strong) NSTextField *askField;
@property (nonatomic, strong) SprigBubble *bubble;
@property (nonatomic, strong) NSTextField *bubbleLabel;
@property (nonatomic, strong) NSTimer *eyeTimer, *blinkTimer, *bubbleTimer, *moodTimer;
@property (nonatomic, copy) void (^action)(NSString *);
@property (nonatomic, copy) NSString *mood;
@property (nonatomic, assign) BOOL hovering, asking, dragging;
@property (nonatomic, assign) NSPoint anchor;   // bottom centre of the character, in screen coordinates
@end

@implementation PipkinPet

- (instancetype)initWithImage:(NSImage *)image action:(void (^)(NSString *))action {
    self = [super init];
    if (!self) return nil;
    _action = [action copy];
    _mood = @"idle";

    _panel = [[SprigPanel alloc] initWithContentRect:NSMakeRect(0, 0, 104, 104)
        styleMask:NSWindowStyleMaskBorderless | NSWindowStyleMaskNonactivatingPanel
        backing:NSBackingStoreBuffered defer:NO];
    _panel.opaque = NO;
    _panel.backgroundColor = NSColor.clearColor;
    _panel.hasShadow = NO;
    _panel.floatingPanel = YES;
    _panel.hidesOnDeactivate = NO;
    _panel.becomesKeyOnlyIfNeeded = YES;
    _panel.excludedFromWindowsMenu = YES;
    _panel.level = NSFloatingWindowLevel;
    _panel.collectionBehavior = NSWindowCollectionBehaviorCanJoinAllSpaces | NSWindowCollectionBehaviorFullScreenAuxiliary |
                                NSWindowCollectionBehaviorIgnoresCycle;
    _panel.accessibilityLabel = @"Sprig, Pipkin's desktop companion";

    _root = [[SprigRootView alloc] initWithFrame:NSMakeRect(0, 0, 104, 104)];
    _root.owner = self;
    _panel.contentView = _root;

    _sprig = [[SprigView alloc] initWithFrame:NSMakeRect(0, 0, 96, kCharHeight)];
    _sprig.owner = self;
    _sprig.toolTip = @"Sprig · click to open Pipkin, drag to move";
    [_root addSubview:_sprig];

    [self buildPill];
    [self buildBubble];

    NSUserDefaults *defaults = NSUserDefaults.standardUserDefaults;
    if ([defaults objectForKey:@"SprigAnchorX"]) {
        _anchor = NSMakePoint([defaults doubleForKey:@"SprigAnchorX"], [defaults doubleForKey:@"SprigAnchorY"]);
    } else {
        NSRect usable = NSScreen.mainScreen.visibleFrame;
        _anchor = NSMakePoint(NSMaxX(usable) - 80, NSMinY(usable) + NSHeight(usable) * 0.28);
    }
    [[NSNotificationCenter defaultCenter] addObserver:self selector:@selector(screenChanged:)
        name:NSApplicationDidChangeScreenParametersNotification object:nil];
    return self;
}

- (void)dealloc {
    [[NSNotificationCenter defaultCenter] removeObserver:self];
    [self stopTimers];
}

#pragma mark Building

- (NSButton *)symbol:(NSString *)name tip:(NSString *)tip action:(SEL)selector {
    NSImage *image = [NSImage imageWithSystemSymbolName:name accessibilityDescription:tip];
    NSImageSymbolConfiguration *config = [NSImageSymbolConfiguration configurationWithPointSize:15 weight:NSFontWeightMedium];
    NSButton *button = [NSButton buttonWithImage:[image imageWithSymbolConfiguration:config] ?: image target:self action:selector];
    button.bordered = NO;
    button.toolTip = tip;
    button.contentTintColor = NSColor.labelColor;
    button.accessibilityLabel = tip;
    return button;
}

- (void)buildPill {
    _pill = [[NSVisualEffectView alloc] initWithFrame:NSMakeRect(0, 0, 168, 40)];
    _pill.material = NSVisualEffectMaterialPopover;
    _pill.state = NSVisualEffectStateActive;
    _pill.wantsLayer = YES;
    _pill.layer.cornerRadius = 20;
    _pill.layer.masksToBounds = YES;
    _pill.layer.borderWidth = 0.5;
    _pill.layer.borderColor = Hex(0x000000, 0.12);
    _pill.hidden = YES;

    _buttonRow = [[NSView alloc] initWithFrame:NSMakeRect(0, 0, 168, 40)];
    NSArray *buttons = @[
        [self symbol:@"square.and.pencil" tip:@"New chat" action:@selector(newChat:)],
        [self symbol:@"text.bubble" tip:@"Ask Sprig" action:@selector(startAsk:)],
        [self symbol:@"terminal" tip:@"Agent" action:@selector(openAgent:)],
        [self symbol:@"xmark" tip:@"Hide Sprig" action:@selector(hideSprig:)],
    ];
    CGFloat x = 8;
    for (NSUInteger index = 0; index < buttons.count; index++) {
        NSButton *button = buttons[index];
        if (index == buttons.count - 1) {
            NSBox *divider = [[NSBox alloc] initWithFrame:NSMakeRect(x + 1, 11, 1, 18)];
            divider.boxType = NSBoxSeparator;
            [_buttonRow addSubview:divider];
            x += 6;
            button.contentTintColor = NSColor.secondaryLabelColor;
        }
        button.frame = NSMakeRect(x, 4, 34, 32);
        [_buttonRow addSubview:button];
        x += 36;
    }
    [_pill addSubview:_buttonRow];

    _askRow = [[NSView alloc] initWithFrame:NSMakeRect(0, 0, 240, 40)];
    _askField = [[NSTextField alloc] initWithFrame:NSMakeRect(16, 10, 180, 20)];
    _askField.placeholderString = @"Ask Sprig…";
    _askField.bezeled = NO;
    _askField.drawsBackground = NO;
    _askField.focusRingType = NSFocusRingTypeNone;
    _askField.font = [NSFont systemFontOfSize:13];
    _askField.delegate = self;
    _askField.target = self;
    _askField.action = @selector(sendAsk:);
    _askField.cell.scrollable = YES;
    [_askRow addSubview:_askField];
    NSButton *send = [self symbol:@"arrow.up.circle.fill" tip:@"Send to Pipkin" action:@selector(sendAsk:)];
    send.contentTintColor = [NSColor colorWithSRGBRed:0.12 green:0.48 blue:0.33 alpha:1];
    send.frame = NSMakeRect(202, 4, 32, 32);
    [_askRow addSubview:send];
    _askRow.hidden = YES;
    [_pill addSubview:_askRow];
    [_root addSubview:_pill];
}

- (void)buildBubble {
    _bubble = [[SprigBubble alloc] initWithFrame:NSZeroRect];
    _bubble.owner = self;
    _bubble.material = NSVisualEffectMaterialPopover;
    _bubble.state = NSVisualEffectStateActive;
    _bubble.wantsLayer = YES;
    _bubble.layer.cornerRadius = 12;
    _bubble.layer.masksToBounds = YES;
    _bubble.layer.borderWidth = 0.5;
    _bubble.layer.borderColor = Hex(0x000000, 0.12);
    _bubble.hidden = YES;
    _bubbleLabel = [NSTextField wrappingLabelWithString:@""];
    _bubbleLabel.font = [NSFont systemFontOfSize:12 weight:NSFontWeightMedium];
    _bubbleLabel.maximumNumberOfLines = 3;
    _bubbleLabel.lineBreakMode = NSLineBreakByTruncatingTail;
    [_bubble addSubview:_bubbleLabel];
    [_root addSubview:_bubble];
}

#pragma mark Visibility & layout

- (void)setVisible:(BOOL)visible {
    _visible = visible;
    if (visible) {
        [self layout];
        [self.panel orderFrontRegardless];
        [self startTimers];
    } else {
        [self endAsk];
        self.hovering = NO;
        [self stopTimers];
        [self.panel orderOut:nil];
    }
}

- (void)setDocked:(BOOL)docked {
    _docked = docked;
    self.sprig.sleeping = docked;
    [self.sprig applyMood];
}

- (void)setMood:(NSString *)mood {
    [self.moodTimer invalidate];
    _mood = [(mood ?: @"idle") copy];   // direct ivar: this method is the property's setter
    self.sprig.mood = self.mood;
    [self.sprig applyMood];
    if (!self.visible || self.docked) return;
    if ([mood isEqual:@"attention"] || [mood isEqual:@"happy"]) [self.sprig hop];
    if ([mood isEqual:@"happy"]) {
        __weak typeof(self) weak = self;
        self.moodTimer = [NSTimer scheduledTimerWithTimeInterval:4 repeats:NO block:^(NSTimer *timer) {
            if ([weak.mood isEqual:@"happy"]) [weak setMood:@"idle"];
        }];
    }
}

- (void)say:(NSString *)text {
    if (!text.length) return;
    self.bubbleLabel.stringValue = text.length > 120 ? [[text substringToIndex:119] stringByAppendingString:@"…"] : text;
    self.bubble.hidden = NO;
    [self layout];
    [self.bubbleTimer invalidate];
    __weak typeof(self) weak = self;
    self.bubbleTimer = [NSTimer scheduledTimerWithTimeInterval:7 repeats:NO block:^(NSTimer *timer) {
        weak.bubble.hidden = YES;
        [weak layout];
    }];
}

- (void)screenChanged:(NSNotification *)notification {
    if (self.visible) [self layout];
}

- (NSPoint)clampedAnchor:(NSPoint)anchor {
    NSScreen *target = NSScreen.mainScreen;
    for (NSScreen *screen in NSScreen.screens) {
        if (NSPointInRect(anchor, NSInsetRect(screen.frame, -1, -1))) target = screen;
    }
    NSRect usable = target.visibleFrame;
    anchor.x = MIN(MAX(anchor.x, NSMinX(usable) + 44), NSMaxX(usable) - 44);
    anchor.y = MIN(MAX(anchor.y, NSMinY(usable) + 4), NSMaxY(usable) - kCharHeight - 4);
    return anchor;
}

- (void)layout {
    BOOL pill = (self.hovering || self.asking) && !self.dragging && !self.docked;
    BOOL bubble = !self.bubble.hidden;
    CGFloat pillWidth = self.asking ? 240 : 168, pillHeight = 40, gap = 6;
    NSSize text = NSZeroSize;
    if (bubble) {
        self.bubbleLabel.preferredMaxLayoutWidth = 196;
        text = self.bubbleLabel.fittingSize;
        text.width = MIN(MAX(text.width, 40), 196);
    }
    NSSize bubbleSize = bubble ? NSMakeSize(text.width + 24, text.height + 16) : NSZeroSize;
    CGFloat width = MAX(96, MAX(pill ? pillWidth : 0, bubbleSize.width)) + 8;
    CGFloat below = pill ? pillHeight + gap : 0;
    CGFloat height = below + kCharHeight + (bubble ? bubbleSize.height + gap : 0);
    if (!self.dragging) self.anchor = [self clampedAnchor:self.anchor];
    NSRect frame = NSMakeRect(round(self.anchor.x - width / 2), round(self.anchor.y - below), width, height);
    // Keep the pill and bubble on screen even when Sprig sits near an edge; Sprig itself stays put.
    NSRect usable = (self.panel.screen ?: NSScreen.mainScreen).visibleFrame;
    if (!self.dragging) frame.origin.x = MIN(MAX(NSMinX(frame), NSMinX(usable) + 4), NSMaxX(usable) - width - 4);
    CGFloat center = self.anchor.x - NSMinX(frame);
    [self.panel setFrame:frame display:YES];
    self.root.frame = NSMakeRect(0, 0, width, height);
    self.sprig.frame = NSMakeRect(round(center - 48), below, 96, kCharHeight);
    self.pill.hidden = !pill;
    self.pill.frame = NSMakeRect(round(MIN(MAX(center - pillWidth / 2, 0), width - pillWidth)), 0, pillWidth, pillHeight);
    self.buttonRow.hidden = self.asking;
    self.askRow.hidden = !self.asking;
    if (bubble) {
        CGFloat bubbleX = MIN(MAX(center - bubbleSize.width / 2, 0), width - bubbleSize.width);
        self.bubble.frame = NSMakeRect(round(bubbleX), below + kCharHeight - 10 + gap, bubbleSize.width, bubbleSize.height);
        self.bubbleLabel.frame = NSMakeRect(12, 8, text.width, text.height);
    }
}

#pragma mark Timers

- (void)startTimers {
    [self stopTimers];
    __weak typeof(self) weak = self;
    self.eyeTimer = [NSTimer timerWithTimeInterval:1.0 / 30 repeats:YES block:^(NSTimer *timer) {
        [weak.sprig lookAt:NSEvent.mouseLocation];
    }];
    [NSRunLoop.mainRunLoop addTimer:self.eyeTimer forMode:NSRunLoopCommonModes];
    [self scheduleBlink];
}

- (void)scheduleBlink {
    __weak typeof(self) weak = self;
    self.blinkTimer = [NSTimer scheduledTimerWithTimeInterval:2.5 + arc4random_uniform(3500) / 1000.0 repeats:NO block:^(NSTimer *timer) {
        [weak.sprig blink];
        if (arc4random_uniform(4) == 0) {
            dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(0.25 * NSEC_PER_SEC)), dispatch_get_main_queue(), ^{ [weak.sprig blink]; });
        }
        if (weak.visible) [weak scheduleBlink];
    }];
}

- (void)stopTimers {
    [self.eyeTimer invalidate];
    [self.blinkTimer invalidate];
    self.eyeTimer = nil;
    self.blinkTimer = nil;
}

#pragma mark Interaction

- (void)perform:(NSString *)action {
    if (self.action) self.action(action);
}

- (void)sprigClicked { [self perform:@"open"]; }
- (void)bubbleClicked {
    self.bubble.hidden = YES;
    [self layout];
    [self perform:@"open"];
}

- (void)sprigDragFrom:(NSPoint)startAnchor delta:(NSPoint)delta {
    if (NSEqualPoints(delta, NSZeroPoint)) {
        self.sprig.startAnchor = self.anchor;
        return;
    }
    if (!self.dragging) {
        self.dragging = YES;
        [self endAsk];
        [self layout];
    }
    self.anchor = NSMakePoint(startAnchor.x + delta.x, startAnchor.y + delta.y);
    [self layout];
}

- (void)sprigDragEnded {
    self.dragging = NO;
    self.anchor = [self clampedAnchor:self.anchor];
    [NSUserDefaults.standardUserDefaults setDouble:self.anchor.x forKey:@"SprigAnchorX"];
    [NSUserDefaults.standardUserDefaults setDouble:self.anchor.y forKey:@"SprigAnchorY"];
    [self layout];
}

- (void)hoverChanged:(BOOL)inside {
    if (!self.visible) return;
    if (inside) {
        if (!self.hovering && !self.docked) {
            self.hovering = YES;
            [self layout];
        }
        return;
    }
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(0.45 * NSEC_PER_SEC)), dispatch_get_main_queue(), ^{
        if (!self.hovering || self.asking || NSPointInRect(NSEvent.mouseLocation, self.panel.frame)) return;
        self.hovering = NO;
        [self layout];
    });
}

- (void)newChat:(id)sender { [self perform:@"new"]; }
- (void)openAgent:(id)sender { [self perform:@"work"]; }
- (void)hideSprig:(id)sender { [self perform:@"hide"]; }

- (void)startAsk:(id)sender {
    self.asking = YES;
    [self layout];
    [self.panel makeKeyAndOrderFront:nil];
    [self.panel makeFirstResponder:self.askField];
}

- (void)endAsk {
    if (!self.asking) return;
    self.asking = NO;
    self.askField.stringValue = @"";
    if (self.panel.isKeyWindow) [self.panel resignKeyWindow];
    [self layout];
}

- (void)sendAsk:(id)sender {
    NSString *text = [self.askField.stringValue stringByTrimmingCharactersInSet:NSCharacterSet.whitespaceAndNewlineCharacterSet];
    if (!text.length) return;
    [self endAsk];
    self.hovering = NO;
    [self layout];
    [self.sprig hop];
    [self perform:[@"ask:" stringByAppendingString:text]];
}

- (BOOL)control:(NSControl *)control textView:(NSTextView *)textView doCommandBySelector:(SEL)command {
    if (command == @selector(cancelOperation:)) {
        [self endAsk];
        return YES;
    }
    return NO;
}

- (void)showMenu:(NSEvent *)event inView:(NSView *)view {
    NSMenu *menu = [[NSMenu alloc] initWithTitle:@"Sprig"];
    NSArray *items = @[@[@"Open Pipkin", @"open"], @[@"New Chat", @"new"], @[@"Ask Sprig…", @"ask"], @[@"Chat", @"chat"],
                       @[@"Agent", @"work"], @[@"Skills", @"skills"], @[@"-", @""], @[@"Snooze for 1 Hour", @"snooze"],
                       @[@"Hide Sprig", @"hide"]];
    for (NSArray *pair in items) {
        if ([pair[0] isEqual:@"-"]) {
            [menu addItem:NSMenuItem.separatorItem];
            continue;
        }
        NSMenuItem *item = [[NSMenuItem alloc] initWithTitle:pair[0] action:@selector(menuChoice:) keyEquivalent:@""];
        item.target = self;
        item.representedObject = pair[1];
        [menu addItem:item];
    }
    [NSMenu popUpContextMenu:menu withEvent:event forView:view];
}

- (void)menuChoice:(NSMenuItem *)item {
    NSString *choice = item.representedObject;
    if ([choice isEqual:@"ask"]) {
        self.hovering = YES;
        [self startAsk:nil];
    } else if ([choice isEqual:@"snooze"]) {
        [self perform:@"hide-temporarily"];
    } else {
        [self perform:choice];
    }
}

@end
