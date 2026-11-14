import "./globals.css";

export const metadata = {
  title: "WorkPilot",
  description: "A business workspace for knowledge documents, sales records, and customer enquiries.",
};

export default function RootLayout({ children }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
