import AppKit

// A failed launch never invokes terminationHandler, so ownership stays here.
func runLoggedProcess(_ process: Process, output: FileHandle) throws {
    do {
        try process.run()
    } catch {
        try? output.close()
        throw error
    }
}

// A fixed action only: URLs can never supply commands, paths, or device hosts.
final class CastleLauncher: NSObject, NSApplicationDelegate {
    private var task: Process?
    private var statusItem: NSStatusItem?
    private var logURL: URL?

    func applicationWillFinishLaunching(_: Notification) {
        NSAppleEventManager.shared().setEventHandler(
            self, andSelector: #selector(openURL(_:reply:)),
            forEventClass: AEEventClass(kInternetEventClass),
            andEventID: AEEventID(kAEGetURL))
    }

    func applicationDidFinishLaunching(_: Notification) {
        let item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        item.button?.title = "♜"
        item.button?.toolTip = "Castle Tools"
        let menu = NSMenu()
        for (title, action) in [
            ("Start Mac tools", #selector(startTools)),
            ("Open startup log", #selector(openLog)),
            ("Quit Castle Tools", #selector(quitTools)),
        ] {
            let entry = NSMenuItem(title: title, action: action, keyEquivalent: "")
            entry.target = self
            menu.addItem(entry)
        }
        item.menu = menu
        statusItem = item
        // Finder launch is also useful; URL delivery reuses this same process.
        startTools()
    }

    @objc func openURL(_ event: NSAppleEventDescriptor, reply _: NSAppleEventDescriptor) {
        guard event.paramDescriptor(forKeyword: keyDirectObject)?.stringValue
                == "castle-tools://start" else { return }
        startTools()
    }

    @objc func startTools() {
        if task?.isRunning == true { return }
        guard let root = Bundle.main.object(forInfoDictionaryKey: "CastleProjectPath") as? String
        else { fail("Re-register Castle Tools from your project folder."); return }
        do {
            let logs = FileManager.default.homeDirectoryForCurrentUser
                .appendingPathComponent("Library/Logs/Castle Tools", isDirectory: true)
            try FileManager.default.createDirectory(at: logs, withIntermediateDirectories: true)
            let log = logs.appendingPathComponent("startup.log")
            if !FileManager.default.fileExists(atPath: log.path) {
                FileManager.default.createFile(atPath: log.path, contents: nil)
            }
            let output = try FileHandle(forWritingTo: log)
            try output.seekToEnd()
            logURL = log
            let process = Process()
            process.executableURL = URL(fileURLWithPath: "/bin/bash")
            process.arguments = [URL(fileURLWithPath: root)
                .appendingPathComponent("Open Castle Studio.command").path]
            var environment = ProcessInfo.processInfo.environment
            environment["CASTLE_STUDIO_NO_BROWSER"] = "1"
            process.environment = environment
            process.standardInput = FileHandle.nullDevice
            process.standardOutput = output
            process.standardError = output
            process.terminationHandler = { [weak self] ended in
                try? output.close()
                DispatchQueue.main.async {
                    guard let self = self else { return }
                    self.task = nil
                    if ended.terminationStatus != 0 {
                        self.fail("Castle Tools could not start. Open the startup log for setup or repair instructions.")
                    }
                }
            }
            try runLoggedProcess(process, output: output)
            task = process
        } catch {
            fail(error.localizedDescription)
        }
    }

    private func fail(_ message: String) {
        NSApp.activate(ignoringOtherApps: true)
        let alert = NSAlert()
        alert.messageText = "Castle Tools needs attention"
        alert.informativeText = message
        alert.addButton(withTitle: "OK")
        if logURL != nil { alert.addButton(withTitle: "Open log") }
        if alert.runModal() == .alertSecondButtonReturn { openLog() }
    }

    @objc func openLog() {
        if let log = logURL { NSWorkspace.shared.open(log) }
    }

    @objc func quitTools() { NSApp.terminate(nil) }

    func applicationWillTerminate(_: Notification) {
        // The shell's TERM trap stops only the server it started. A pre-existing
        // helper is reused and is never adopted or stopped by this app.
        if task?.isRunning == true { task?.terminate() }
    }
}

#if !CASTLE_LAUNCHER_TEST
@main
struct CastleToolsMain {
    static func main() {
        let app = NSApplication.shared
        let launcher = CastleLauncher()
        app.delegate = launcher
        app.run()
    }
}
#endif
