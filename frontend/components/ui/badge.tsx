import { cn } from "@/lib/utils";

const variantClasses = {
  success: "bg-green-50 text-green-700 dark:bg-green-950/50 dark:text-green-400",
  warning: "bg-amber-50 text-amber-700 dark:bg-amber-950/50 dark:text-amber-400",
  error: "bg-red-50 text-red-700 dark:bg-red-950/50 dark:text-red-400",
  neutral: "bg-neutral-100 text-neutral-600 dark:bg-neutral-800 dark:text-neutral-400",
};

type BadgeProps = {
  variant?: keyof typeof variantClasses;
  children: React.ReactNode;
  className?: string;
};

export function Badge({ variant = "neutral", children, className }: BadgeProps) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium",
        variantClasses[variant],
        className,
      )}
    >
      {children}
    </span>
  );
}
