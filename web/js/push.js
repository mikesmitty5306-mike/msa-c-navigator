// Push notifications without a server (see "ADDITION: PUSH NOTIFICATIONS" in reports/static_plan.txt).
// The phone subscribes with the public key from data/meta.json and shares the subscription "code" with the
// helper, who stores it as a GitHub secret. GitHub Actions then sends the pushes.

import { urlBase64ToUint8Array } from "./util.js";

export function isStandalone() {
  return (typeof matchMedia === "function" && matchMedia("(display-mode: standalone)").matches) || navigator.standalone === true;
}

export function pushSupported() {
  return "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;
}

/**
 * Why notifications can or can't be offered right now.
 * Returns { state: "ready"|"on"|"not-installed"|"unsupported"|"not-configured"|"blocked", message }
 */
export async function pushStatus(meta) {
  if (!isStandalone()) {
    return { state: "not-installed", message: "Notifications work only in the Home Screen app. Add the app to your Home Screen first (see Install on iPhone), then open it from the icon." };
  }
  if (!pushSupported()) {
    return { state: "unsupported", message: "This iPhone can't receive notifications from web apps. It needs iOS 16.4 or later." };
  }
  if (!meta || !meta.push_public_key) {
    return { state: "not-configured", message: "Notifications aren't set up yet. Your helper needs to finish the setup first." };
  }
  if (Notification.permission === "denied") {
    return { state: "blocked", message: "Notifications are turned off for this app. To allow them: iPhone Settings > Notifications > MSA-C Navigator." };
  }
  const reg = await navigator.serviceWorker.ready;
  const sub = await reg.pushManager.getSubscription();
  return sub ? { state: "on", message: "Notifications are on." } : { state: "ready", message: "" };
}

/** Must be called from a tap. Returns the subscription code (JSON text). */
export async function turnOn(meta) {
  const permission = await Notification.requestPermission();
  if (permission !== "granted") {
    throw new Error(permission === "denied"
      ? "Notifications were not allowed. You can allow them later in iPhone Settings > Notifications > MSA-C Navigator."
      : "Notifications were not turned on.");
  }
  const reg = await navigator.serviceWorker.ready;
  let sub = await reg.pushManager.getSubscription();
  if (!sub) {
    sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: urlBase64ToUint8Array(meta.push_public_key) });
  }
  return JSON.stringify(sub.toJSON());
}

export async function turnOff() {
  const reg = await navigator.serviceWorker.ready;
  const sub = await reg.pushManager.getSubscription();
  if (sub) await sub.unsubscribe();
}

export const SHARE_INSTRUCTIONS = [
  "Notification code for MSA-C Navigator.",
  "Helper: in the GitHub repository, set the Actions secret PUSH_SUBSCRIPTION to the code below, exactly as it is (replace any older code).",
  "This code only lets the app send notifications to this iPhone. It contains no name, email or location.",
].join("\n");

/** Share the code with the Share sheet. Returns "shared", "cancelled" or "unavailable". */
export async function shareCode(code) {
  if (!navigator.share) return "unavailable";
  try {
    await navigator.share({ title: "MSA-C Navigator notification code", text: `${SHARE_INSTRUCTIONS}\n\n${code}` });
    return "shared";
  } catch (e) {
    return e && e.name === "AbortError" ? "cancelled" : "unavailable";
  }
}
