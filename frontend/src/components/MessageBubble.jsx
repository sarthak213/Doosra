export default function MessageBubble({ text }) {
  return (
    <div className="message-row">
      <div className="message-bubble">{text}</div>
    </div>
  );
}
