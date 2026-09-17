import Foundation

// Compile with the real launcher and -D CASTLE_LAUNCHER_TEST on macOS.
@main
struct LauncherProcessTest {
    static func main() throws {
        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent("castle-launch-test-\(UUID().uuidString)")
        FileManager.default.createFile(atPath: url.path, contents: nil)
        defer { try? FileManager.default.removeItem(at: url) }
        let output = try FileHandle(forWritingTo: url)
        let process = Process()
        process.executableURL = url.appendingPathComponent("missing-executable")
        process.standardOutput = output
        do {
            try runLoggedProcess(process, output: output)
            fatalError("A nonexistent executable unexpectedly launched")
        } catch {
            // The original launch error is preserved; its log must be closed.
        }
        do {
            try output.write(contentsOf: Data("must not write".utf8))
            fatalError("Failed launch leaked an open log handle")
        } catch {
            print("PASS: failed launch closes its log handle")
        }
    }
}
