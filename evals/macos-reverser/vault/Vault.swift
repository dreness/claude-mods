import Foundation

open class SessionStore {
    public let createdAt: Double
    public var token: String
    public var retries: Int
    public init(token: String) { createdAt = Date().timeIntervalSince1970; self.token = token; retries = 0 }
    open func describe() -> String { "\(token):\(retries)" }
    open func reset() { token = ""; retries = 0 }
    open func refresh(with newToken: String) { token = newToken; retries += 1 }
}

final class EphemeralStore: SessionStore {
    override func reset() { token = "ephemeral"; retries = -1 }
}

open class AuditLog {
    public let path: String
    public var lines: [String] = []
    public var rotations = 0
    public init() { path = "/tmp/audit" }
    open func describe() -> String { "\(lines.count)" }
    open func reset() { lines.removeAll(); rotations += 1 }
}

@inline(never) func logout(_ s: SessionStore) { s.reset() }
@inline(never) func handleAuthFailure(_ s: SessionStore, code: Int) { if code == 401 { s.reset() } else { s.retries += 1 } }
@inline(never) func adoptToken(_ s: SessionStore, _ t: String) { s.token = t }
@inline(never) func rotateLogs(_ log: AuditLog) { log.reset() }
@inline(never) func summary(_ s: SessionStore) -> String { s.describe() }

public final class AppController {
    let store: SessionStore
    let log = AuditLog()
    public init(ephemeral: Bool) { store = ephemeral ? EphemeralStore(token: "x") : SessionStore(token: "y") }
    @inline(never) public func applicationWillTerminate() { store.reset(); rotateLogs(log) }
    @inline(never) public func run(_ args: [String]) {
        if args.contains("logout") { logout(store) }
        if args.contains("fail") { handleAuthFailure(store, code: args.count) }
        if let t = args.last { adoptToken(store, t); store.refresh(with: t + "!") }
        print(summary(store))
    }
}

let app = AppController(ephemeral: CommandLine.arguments.contains("-e"))
app.run(CommandLine.arguments)
app.applicationWillTerminate()
