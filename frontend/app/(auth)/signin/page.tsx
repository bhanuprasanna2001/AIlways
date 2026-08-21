import { redirect } from "next/navigation";
import { getMe } from "@/lib/auth-server";
import SignInForm from "./signin-form";

export default async function SignInPage() {
  const user = await getMe();
  if (user) redirect("/dashboard");

  return (
    <div className="flex min-h-screen items-center justify-center bg-background px-6">
      <div className="w-full max-w-sm">
        <div className="mb-8 text-center">
          <h1 className="text-2xl font-bold text-foreground">Welcome back</h1>
          <p className="mt-2 text-sm text-neutral-500 dark:text-neutral-400">
            Sign in to your account
          </p>
        </div>
        <SignInForm />
      </div>
    </div>
  );
}
