import { Route, Routes } from "react-router-dom";
import Sidebar from "./components/Sidebar";
import MeetingPage from "./routes/MeetingPage";
import SettingsPage from "./routes/SettingsPage";

function Home() {
  return (
    <main className="workspace">
      <div className="home">
        <img className="home__logo" src="/skyroot_logo.png" alt="Skyroot Aerospace" />
        <div className="home__mark">SHRUTI</div>
        <div className="home__deva">श्रुति — that which is heard</div>
        <p className="home__line">
          Select a recording from the log, or upload one to get a transcript, minutes, and answers.
        </p>
      </div>
    </main>
  );
}

export default function App() {
  return (
    <div className="shell">
      <Sidebar />
      <Routes>
        <Route path="/" element={<Home />} />
        <Route path="/m/:id" element={<MeetingPage />} />
        <Route path="/settings" element={<SettingsPage />} />
      </Routes>
    </div>
  );
}
