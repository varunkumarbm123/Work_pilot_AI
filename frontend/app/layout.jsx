import "./globals.css";

export const metadata = {
  title: "WorkPilot AI",
  description: "A human-guided AI workforce for customer enquiries and sales reporting.",
};

export default function RootLayout({ children }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
