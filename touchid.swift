import Foundation
import LocalAuthentication

let context = LAContext()
context.localizedFallbackTitle = ""
var error: NSError?
guard context.canEvaluatePolicy(.deviceOwnerAuthenticationWithBiometrics, error: &error),
      context.biometryType == .touchID else {
    exit(2)
}

guard CommandLine.arguments.count == 2 else { exit(3) }
if CommandLine.arguments[1] == "check" { exit(0) }
guard CommandLine.arguments[1] == "authenticate" else { exit(3) }

let finished = DispatchSemaphore(value: 0)
var approved = false
context.evaluatePolicy(
    .deviceOwnerAuthenticationWithBiometrics,
    localizedReason: "Desbloquear o Meu Dinheiro"
) { success, _ in
    approved = success
    finished.signal()
}

if finished.wait(timeout: .now() + 90) == .timedOut {
    context.invalidate()
    exit(4)
}
exit(approved ? 0 : 1)
