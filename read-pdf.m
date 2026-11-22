#import <Foundation/Foundation.h>
#import <PDFKit/PDFKit.h>

int main(int argc, const char *argv[]) {
    @autoreleasepool {
        if (argc != 2) return 2;
        NSURL *url = [NSURL fileURLWithPath:[NSString stringWithUTF8String:argv[1]]];
        PDFDocument *document = [[PDFDocument alloc] initWithURL:url];
        if (!document || [document isLocked]) return 2;

        NSMutableString *result = [NSMutableString string];
        for (NSUInteger i = 0; i < [document pageCount] && [result length] <= 8500; i++) {
            NSString *text = [[document pageAtIndex:i] string];
            if ([text length]) {
                [result appendString:[text substringToIndex:MIN([text length], 8501)]];
                [result appendString:@"\n\n"];
            }
        }
        NSData *data = [result dataUsingEncoding:NSUTF8StringEncoding allowLossyConversion:YES];
        [[NSFileHandle fileHandleWithStandardOutput] writeData:data];
    }
    return 0;
}
