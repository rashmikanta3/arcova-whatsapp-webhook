# arcova-whatsapp-webhook
web hook with whatsupapi

# The SQL query to create Table

CREATE TABLE IF NOT EXISTS messages (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    phone TEXT NOT NULL,
    sender TEXT NOT NULL,
    message_text TEXT NOT NULL,
    timestamp TEXT NOT NULL
);

-- Enable Row Level Security and allow full access for the API keys
ALTER TABLE messages ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Allow full access" ON messages FOR ALL USING (true) WITH CHECK (true);
