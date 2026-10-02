"use client";

import dynamic from "next/dynamic";
import remarkGfm from "remark-gfm";

const ReactMarkdown = dynamic(() => import("react-markdown"), { ssr: false });

type MarkdownBlockProps = {
  content: string;
  className?: string;
};

export function MarkdownBlock({ content, className = "" }: MarkdownBlockProps) {
  return (
    <div className={className}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          p: ({ children }) => <p className="mb-2 break-words last:mb-0 text-inherit leading-relaxed">{children}</p>,
          ul: ({ children }) => <ul className="mb-2 list-disc space-y-1 pl-5 text-inherit">{children}</ul>,
          ol: ({ children }) => <ol className="mb-2 list-decimal space-y-1 pl-5 text-inherit">{children}</ol>,
          li: ({ children }) => <li className="break-words leading-relaxed">{children}</li>,
          code: ({ children, className: codeClassName }) => {
            const isBlock = typeof codeClassName === "string" && /language-/.test(codeClassName);
            if (isBlock) {
              return <code className="font-mono text-[11px] text-inherit">{children}</code>;
            }
            return (
              <code className="rounded bg-[hsl(var(--muted)/0.65)] px-1 py-0.5 font-mono text-[11px] text-inherit">{children}</code>
            );
          },
          pre: ({ children }) => (
            <pre className="mb-2 overflow-x-auto rounded-md border border-[var(--ui-border)] bg-[hsl(var(--muted)/0.5)] p-2 text-[11px] text-inherit">
              {children}
            </pre>
          ),
          h1: ({ children }) => <h3 className="mb-2 mt-3 text-base font-semibold text-inherit">{children}</h3>,
          h2: ({ children }) => <h4 className="mb-2 mt-3 text-sm font-semibold text-inherit">{children}</h4>,
          h3: ({ children }) => <h5 className="mb-1 mt-2 text-sm font-semibold text-inherit">{children}</h5>,
          blockquote: ({ children }) => (
            <blockquote className="mb-2 border-l-2 border-[var(--ui-border)] pl-3 text-inherit opacity-90">{children}</blockquote>
          ),
          a: ({ children, href }) => (
            <a href={href} className="underline decoration-dotted underline-offset-2 text-inherit">
              {children}
            </a>
          ),
          table: ({ children }) => (
            <div className="mb-2 overflow-x-auto">
              <table className="min-w-full border-collapse text-left text-inherit">{children}</table>
            </div>
          ),
          thead: ({ children }) => <thead className="border-b border-[var(--ui-border)] text-inherit">{children}</thead>,
          tbody: ({ children }) => <tbody className="text-inherit">{children}</tbody>,
          th: ({ children }) => <th className="border border-[var(--ui-border)] px-2 py-1 text-xs font-semibold text-inherit">{children}</th>,
          td: ({ children }) => <td className="border border-[var(--ui-border)] px-2 py-1 align-top text-xs text-inherit">{children}</td>,
          hr: () => <hr className="my-3 border-[var(--ui-border)]" />,
          strong: ({ children }) => <strong className="font-semibold text-inherit">{children}</strong>,
          em: ({ children }) => <em className="italic text-inherit">{children}</em>,
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}