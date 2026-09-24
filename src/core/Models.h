#import <Foundation/Foundation.h>
#import "AXState.h"

// One model the gearbox knows about.
//  - Claude: `token` is the /model argument (haiku, sonnet, opus, fable, ...),
//    `display` is what the status line / "Set model to" line shows (e.g. "Opus 5.5").
//  - Codex:  `token` is the picker label (gpt-6-sol, ...); display == token.
//  `efforts` lists the effort tokens this model accepts (Codex is per-model; Claude
//  shares one list). Nil/empty means "use the agent's base list".
@interface ModelEntry : NSObject
@property(nonatomic, copy) NSString *token;
@property(nonatomic, copy) NSString *display;
@property(nonatomic, copy) NSArray<NSString*> *efforts;
@end

// The model catalog: the ONE place that says which models exist, how each is named on
// screen, and which efforts it takes. Every consumer reads from here: the protocol
// planner (expected display, Codex membership), the pane classifier (status-line
// normalization), the gearbox UI (gates and throttle) and the qualification check.
//
// Built-in defaults come from the agents installed on 2026-09-24 (Claude Code 2.1.282,
// Codex 0.156.1). config.toml can add or override entries without recompiling:
//
//   claude_model.opus = "Opus 5.5"
//   codex_model.gpt-6-astra = "low medium high xhigh max ultra"
//
// Entries keep file order after the defaults; the first six of each agent become the
// gearbox gates 1,2,3,4,5,R. Tokens must pass Config's injection-safe charset (they
// become keystrokes); displays must pass a conservative display charset (they reach
// the web view). Anything that fails is ignored — fail closed.
@interface ModelCatalog : NSObject
+ (instancetype)defaults;
// The catalog in effect (set by Config on load; defaults until then).
+ (ModelCatalog *)current;
+ (void)setCurrent:(ModelCatalog *)catalog;

- (NSArray<ModelEntry*> *)entriesForKind:(AgentKind)kind;
- (ModelEntry *)entryForKind:(AgentKind)kind token:(NSString *)token;
// Claude: token -> expected display (falls back to the token itself, which will then
// never verify, so an unknown token refuses instead of guessing).
- (NSString *)claudeDisplayForToken:(NSString *)token;
// Reverse map of a status-line display to its token ("" if unknown).
- (NSString *)tokenForKind:(AgentKind)kind display:(NSString *)display;
// Canonical catalog display for a raw status-line segment, or nil. Tolerates a
// trailing " (...)" / " ..." decoration: "Opus 5.5 (1M context)" -> "Opus 5.5", but
// never a longer version: "Opus 5.5" does NOT match "Opus 5", "Fable 5.1" does NOT
// match "Fable 5".
- (NSString *)canonicalClaudeDisplay:(NSString *)raw;
// Effort tokens for a model (per-model list or the agent's base list).
- (NSArray<NSString*> *)effortsForKind:(AgentKind)kind token:(NSString *)token;
- (BOOL)kind:(AgentKind)kind model:(NSString *)token acceptsEffort:(NSString *)effort;

// config.toml overlay. Returns NO when the line is not a model key at all.
- (BOOL)applyKey:(NSString *)key value:(NSString *)value;

+ (NSArray<NSString*> *)baseEffortsForKind:(AgentKind)kind;
+ (BOOL)isDisplaySafe:(NSString *)s;
// Exact match, or `expect` followed by ' ' or '(' (token boundary).
+ (BOOL)display:(NSString *)raw matches:(NSString *)expect;
@end
