import ErrorBoundary from "./components/ErrorBoundary";
import GridWiseCopilot from "./components/copilot/GridWiseCopilot";
import Home from "./pages/Home";

function App() {
  return (
    <ErrorBoundary>
      <Home />
      <GridWiseCopilot />
    </ErrorBoundary>
  );
}

export default App;
