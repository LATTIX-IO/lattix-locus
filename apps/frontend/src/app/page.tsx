import { redirect } from "next/navigation";

// The shell decides whether a session is needed: the desktop app is always the
// signed-in local operator, and a web profile without a session is sent to /auth.
export default function RootPage() {
  redirect("/home");
}
