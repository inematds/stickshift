#import "Models.h"
#import "Config.h"

@implementation ModelEntry @end

static ModelEntry *E(NSString *token, NSString *display, NSArray<NSString*> *efforts) {
    ModelEntry *e = [ModelEntry new]; e.token = token; e.display = display; e.efforts = efforts; return e;
}

static ModelCatalog *gCurrent;

@implementation ModelCatalog {
    NSMutableArray<ModelEntry*> *_claude;
    NSMutableArray<ModelEntry*> *_codex;
}

+ (NSArray<NSString*> *)baseEffortsForKind:(AgentKind)kind {
    if (kind == AgentClaude) return @[@"low",@"medium",@"high",@"xhigh",@"max",@"ultracode",@"auto"];
    if (kind == AgentCodex)  return @[@"low",@"medium",@"high",@"xhigh",@"max"];
    return @[];
}

// Every effort token either agent can ever take; per-model lists must be a subset.
static NSSet *allEfforts(AgentKind kind) {
    return kind == AgentClaude
        ? [NSSet setWithArray:@[@"low",@"medium",@"high",@"xhigh",@"max",@"ultracode",@"auto"]]
        : [NSSet setWithArray:@[@"low",@"medium",@"high",@"xhigh",@"max",@"ultra"]];
}

+ (instancetype)defaults {
    ModelCatalog *c = [ModelCatalog new];
    // Claude Code 2.1.282: aliases resolve opus -> claude-opus-5-5 ("Opus 5.5"),
    // fable -> claude-fable-5-1 ("Fable 5.1"), sonnet -> "Sonnet 5", haiku -> "Haiku 4.5".
    // `default` is left out on purpose: it resolves per account/plan, so its display
    // cannot be known in advance and a gear on it could never be verified.
    c->_claude = [@[
        E(@"haiku",  @"Haiku 4.5", nil),
        E(@"sonnet", @"Sonnet 5",  nil),
        E(@"opus",   @"Opus 5.5",  nil),
        E(@"fable",  @"Fable 5.1", nil),
    ] mutableCopy];
    // Codex 0.156.1 (~/.codex/models_cache.json, visibility "list"). First six are the
    // gearbox gates; the rest stay valid targets for gear remaps.
    NSArray *toMax   = @[@"low",@"medium",@"high",@"xhigh",@"max"];
    NSArray *toUltra = @[@"low",@"medium",@"high",@"xhigh",@"max",@"ultra"];
    c->_codex = [@[
        E(@"gpt-6-luna",    @"gpt-6-luna",    toMax),
        E(@"gpt-6-sol",     @"gpt-6-sol",     toUltra),
        E(@"gpt-6-astra",   @"gpt-6-astra",   toUltra),
        E(@"gpt-5.6-sol",   @"gpt-5.6-sol",   toUltra),
        E(@"gpt-5.6-terra", @"gpt-5.6-terra", toUltra),
        E(@"gpt-5.5",       @"gpt-5.5",       @[@"low",@"medium",@"high",@"xhigh"]),
        E(@"gpt-5.6-luna",  @"gpt-5.6-luna",  toMax),
        E(@"gpt-5.4",       @"gpt-5.4",       nil),
        E(@"gpt-5.4-mini",  @"gpt-5.4-mini",  nil),
    ] mutableCopy];
    return c;
}

+ (ModelCatalog *)current { return gCurrent ?: (gCurrent = [ModelCatalog defaults]); }
+ (void)setCurrent:(ModelCatalog *)catalog { gCurrent = catalog; }

- (NSMutableArray<ModelEntry*> *)listFor:(AgentKind)kind {
    return kind == AgentClaude ? _claude : (kind == AgentCodex ? _codex : nil);
}

- (NSArray<ModelEntry*> *)entriesForKind:(AgentKind)kind { return [[self listFor:kind] copy] ?: @[]; }

- (ModelEntry *)entryForKind:(AgentKind)kind token:(NSString *)token {
    if (!token.length) return nil;
    for (ModelEntry *e in [self listFor:kind])
        if ([e.token caseInsensitiveCompare:token] == NSOrderedSame) return e;
    return nil;
}

- (NSString *)claudeDisplayForToken:(NSString *)token {
    return [self entryForKind:AgentClaude token:token].display ?: token;
}

+ (BOOL)display:(NSString *)raw matches:(NSString *)expect {
    if (!raw.length || !expect.length) return NO;
    if ([raw caseInsensitiveCompare:expect] == NSOrderedSame) return YES;
    if (raw.length > expect.length
        && [raw compare:expect options:NSCaseInsensitiveSearch range:NSMakeRange(0, expect.length)] == NSOrderedSame) {
        unichar next = [raw characterAtIndex:expect.length];
        return next == ' ' || next == '(';   // "Opus 5.5 (1M context)" yes, "Opus 5.5.1" no
    }
    return NO;
}

- (NSString *)canonicalClaudeDisplay:(NSString *)raw {
    if (!raw.length) return nil;
    // Exact first, so "Opus 5.5" never falls through to a shorter "Opus 5" entry.
    for (ModelEntry *e in _claude) if ([raw caseInsensitiveCompare:e.display] == NSOrderedSame) return e.display;
    // Then the longest decorated match ("Opus 5.5 (1M context)" -> "Opus 5.5").
    ModelEntry *best = nil;
    for (ModelEntry *e in _claude)
        if ([ModelCatalog display:raw matches:e.display] && (!best || e.display.length > best.display.length)) best = e;
    return best.display;
}

- (NSString *)tokenForKind:(AgentKind)kind display:(NSString *)display {
    if (!display.length) return @"";
    if (kind == AgentCodex) return [self entryForKind:AgentCodex token:display] ? display : @"";
    NSString *canon = [self canonicalClaudeDisplay:display];
    for (ModelEntry *e in _claude) if ([e.display isEqualToString:canon]) return e.token;
    return @"";
}

- (NSArray<NSString*> *)effortsForKind:(AgentKind)kind token:(NSString *)token {
    ModelEntry *e = [self entryForKind:kind token:token];
    return e.efforts.count ? e.efforts : [ModelCatalog baseEffortsForKind:kind];
}

- (BOOL)kind:(AgentKind)kind model:(NSString *)token acceptsEffort:(NSString *)effort {
    if (!effort) return YES;                    // model-only gear
    return [[self effortsForKind:kind token:token] containsObject:effort];
}

+ (BOOL)isDisplaySafe:(NSString *)s {
    if (s.length == 0 || s.length > 48) return NO;
    NSCharacterSet *ok = [NSCharacterSet characterSetWithCharactersInString:
        @"abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 .-()"];
    return [s rangeOfCharacterFromSet:ok.invertedSet].location == NSNotFound;
}

- (void)upsert:(ModelEntry *)entry kind:(AgentKind)kind {
    NSMutableArray *list = [self listFor:kind];
    for (NSUInteger i = 0; i < list.count; i++)
        if ([((ModelEntry *)list[i]).token caseInsensitiveCompare:entry.token] == NSOrderedSame) { list[i] = entry; return; }
    [list addObject:entry];
}

// claude_model.<token> = "Display Name"
// codex_model.<label>  = "low medium high xhigh max ultra"   (empty = base list)
// Prefix-parsed: Codex labels contain dots, so the key is never split on '.'.
- (BOOL)applyKey:(NSString *)key value:(NSString *)value {
    AgentKind kind; NSString *token;
    if ([key hasPrefix:@"claude_model."])     { kind = AgentClaude; token = [key substringFromIndex:13]; }
    else if ([key hasPrefix:@"codex_model."]) { kind = AgentCodex;  token = [key substringFromIndex:12]; }
    else return NO;
    if (![Config isInjectionSafe:token]) return YES;          // handled (ignored)
    if (kind == AgentClaude) {
        if (![ModelCatalog isDisplaySafe:value]) return YES;
        [self upsert:E(token, value, nil) kind:AgentClaude];
    } else {
        NSArray *toks = [value componentsSeparatedByCharactersInSet:[NSCharacterSet whitespaceCharacterSet]];
        toks = [toks filteredArrayUsingPredicate:[NSPredicate predicateWithFormat:@"length > 0"]];
        NSSet *allowed = allEfforts(AgentCodex);
        for (NSString *t in toks) if (![allowed containsObject:t]) return YES;   // whole line ignored
        [self upsert:E(token, token, toks.count ? toks : nil) kind:AgentCodex];
    }
    return YES;
}

@end
