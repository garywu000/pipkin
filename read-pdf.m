#import <AppKit/AppKit.h>
#import <CoreText/CoreText.h>
#import <PDFKit/PDFKit.h>

static NSData *RenderPDF(NSArray<NSDictionary *> *blocks) {
    NSMutableAttributedString *text = [[NSMutableAttributedString alloc] init];
    for (NSDictionary *block in blocks) {
        if (![block isKindOfClass:NSDictionary.class]) return nil;
        NSString *kind = block[@"kind"];
        NSString *value = block[@"text"];
        if (![value isKindOfClass:NSString.class] || !value.length) continue;
        BOOL title = [kind isEqualToString:@"title"];
        BOOL heading = [kind isEqualToString:@"heading"];
        BOOL source = [kind isEqualToString:@"source"];
        CGFloat size = title ? 30 : heading ? 16 : source ? 9 : 11;
        NSFont *font = [NSFont systemFontOfSize:size weight:(title || heading ? NSFontWeightSemibold : NSFontWeightRegular)];
        NSColor *color = source ? [NSColor colorWithSRGBRed:.22 green:.38 blue:.32 alpha:1] : NSColor.blackColor;
        NSMutableParagraphStyle *style = [NSMutableParagraphStyle new];
        style.lineSpacing = title || heading ? 2 : 3;
        style.paragraphSpacingBefore = heading ? 12 : 0;
        style.paragraphSpacing = title ? 15 : heading ? 5 : source ? 8 : 10;
        NSAttributedString *line = [[NSAttributedString alloc] initWithString:[value stringByAppendingString:@"\n"]
            attributes:@{NSFontAttributeName:font, NSForegroundColorAttributeName:color, NSParagraphStyleAttributeName:style}];
        [text appendAttributedString:line];
    }
    if (!text.length) return nil;

    NSMutableData *data = [NSMutableData data];
    CGDataConsumerRef consumer = CGDataConsumerCreateWithCFData((__bridge CFMutableDataRef)data);
    if (!consumer) return nil;
    CGRect paper = CGRectMake(0, 0, 612, 792);
    CGContextRef pdf = CGPDFContextCreate(consumer, &paper, NULL);
    CGDataConsumerRelease(consumer);
    if (!pdf) return nil;
    CTFramesetterRef typesetter = CTFramesetterCreateWithAttributedString((__bridge CFAttributedStringRef)text);
    CGPathRef body = CGPathCreateWithRect(CGRectMake(58, 62, 496, 674), NULL);
    CFIndex consumed = 0;
    NSUInteger page = 0;
    while (consumed < (CFIndex)text.length && page < 40) {
        CTFrameRef frame = CTFramesetterCreateFrame(typesetter, CFRangeMake(consumed, 0), body, NULL);
        CFRange visible = CTFrameGetVisibleStringRange(frame);
        if (!visible.length) {
            CFRelease(frame);
            break;
        }
        page++;
        CGPDFContextBeginPage(pdf, NULL);
        CGContextSetTextMatrix(pdf, CGAffineTransformIdentity);
        CTFrameDraw(frame, pdf);
        NSAttributedString *number = [[NSAttributedString alloc] initWithString:[@(page) stringValue]
            attributes:@{NSFontAttributeName:[NSFont systemFontOfSize:9],
                         NSForegroundColorAttributeName:[NSColor colorWithWhite:.34 alpha:1]}];
        CTLineRef footer = CTLineCreateWithAttributedString((__bridge CFAttributedStringRef)number);
        CGContextSetTextPosition(pdf, 542, 35);
        CTLineDraw(footer, pdf);
        CFRelease(footer);
        CGPDFContextEndPage(pdf);
        CFRelease(frame);
        consumed = visible.location + visible.length;
    }
    CGPathRelease(body);
    CFRelease(typesetter);
    CGPDFContextClose(pdf);
    CGContextRelease(pdf);
    if (consumed < (CFIndex)text.length) return nil;
    PDFDocument *verified = [[PDFDocument alloc] initWithData:data];
    return [verified pageCount] == page && page > 0 && [verified string].length ? data : nil;
}

int main(int argc, const char *argv[]) {
    @autoreleasepool {
        if (argc == 2 && strcmp(argv[1], "--render") == 0) {
            NSData *input = [NSFileHandle.fileHandleWithStandardInput readDataToEndOfFile];
            NSArray *blocks = [NSJSONSerialization JSONObjectWithData:input options:0 error:nil];
            if (![blocks isKindOfClass:NSArray.class]) return 2;
            NSData *output = RenderPDF(blocks);
            if (!output) return 3;
            [NSFileHandle.fileHandleWithStandardOutput writeData:output];
            return 0;
        }

        BOOL numbered = argc == 3 && strcmp(argv[1], "--pages") == 0;
        if (!numbered && argc != 2) return 2;
        NSURL *url = [NSURL fileURLWithPath:[NSString stringWithUTF8String:argv[numbered ? 2 : 1]]];
        PDFDocument *document = [[PDFDocument alloc] initWithURL:url];
        if (!document || [document isLocked]) return 2;

        NSMutableString *result = [NSMutableString string];
        for (NSUInteger i = 0; i < [document pageCount] && [result length] <= 8500; i++) {
            NSString *text = [[document pageAtIndex:i] string];
            if (text.length) {
                if (numbered) [result appendFormat:@"## PDF page %lu\n", (unsigned long)(i + 1)];
                [result appendString:[text substringToIndex:MIN(text.length, 8501)]];
                [result appendString:@"\n\n"];
            }
        }
        NSData *data = [result dataUsingEncoding:NSUTF8StringEncoding allowLossyConversion:YES];
        [NSFileHandle.fileHandleWithStandardOutput writeData:data];
    }
    return 0;
}
