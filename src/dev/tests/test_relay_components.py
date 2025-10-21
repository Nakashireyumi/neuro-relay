"""
Unit tests for neuro-relay components
Tests individual components without requiring full server setup
"""

import pytest
import asyncio
import json
from unittest.mock import Mock, AsyncMock, patch
import sys
import os

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from dev.nakurity.client import NakurityClient
from dev.nakurity.intermediary import Intermediary
from dev.nakurity.server import NakurityBackend
from dev.nakurity.linker import NakurityLink

class TestNakurityClient:
    """Test NakurityClient functionality"""
    
    @pytest.fixture
    def mock_websocket(self):
        """Mock websocket for testing"""
        ws = AsyncMock()
        ws.send = AsyncMock()
        ws.recv = AsyncMock()
        ws.close = AsyncMock()
        return ws
    
    @pytest.fixture
    def mock_router_callback(self):
        """Mock router forward callback"""
        return AsyncMock()
    
    def test_client_initialization(self, mock_websocket, mock_router_callback):
        """Test client initializes with correct name"""
        client = NakurityClient(mock_websocket, mock_router_callback)
        assert client.name == "neuro-relay"
        assert client.websocket == mock_websocket
        assert client.router_forward_cb == mock_router_callback
    
    @pytest.mark.asyncio
    async def test_send_startup_command(self, mock_websocket, mock_router_callback):
        """Test startup command sends correct format"""
        client = NakurityClient(mock_websocket, mock_router_callback)
        
        with patch.object(client, 'send_command_data') as mock_send:
            await client.send_startup_command()
            
        # Check that send_command_data was called
        assert mock_send.called
        # The actual command format is handled by the parent class
    
    @pytest.mark.asyncio
    async def test_handle_action(self, mock_websocket, mock_router_callback):
        """Test action handling forwards correctly"""
        client = NakurityClient(mock_websocket, mock_router_callback)
        
        # Create mock action
        mock_action = Mock()
        mock_action.name = "test_action"
        mock_action.data = '{"test": "data"}'
        mock_action.id_ = "action_123"
        
        await client.handle_action(mock_action)
        
        # Verify router callback was called with correct data
        mock_router_callback.assert_called_once()
        args = mock_router_callback.call_args[0][0]
        assert args["from_neuro_backend"] == True
        assert args["action"] == "test_action"
        assert args["data"] == {"test": "data"}
        assert args["id"] == "action_123"
    
    @pytest.mark.asyncio
    async def test_write_to_websocket(self, mock_websocket, mock_router_callback):
        """Test websocket writing"""
        client = NakurityClient(mock_websocket, mock_router_callback)
        test_data = "test message"
        
        await client.write_to_websocket(test_data)
        
        mock_websocket.send.assert_called_once_with(test_data)
    
    @pytest.mark.asyncio
    async def test_read_from_websocket(self, mock_websocket, mock_router_callback):
        """Test websocket reading"""
        client = NakurityClient(mock_websocket, mock_router_callback)
        mock_websocket.recv.return_value = "test response"
        
        result = await client.read_from_websocket()
        
        assert result == "test response"
        mock_websocket.recv.assert_called_once()

class TestIntermediary:
    """Test Intermediary functionality"""
    
    def test_intermediary_initialization(self):
        """Test intermediary initializes correctly"""
        intermediary = Intermediary("127.0.0.1", 8765)
        assert intermediary.host == "127.0.0.1"
        assert intermediary.port == 8765
        assert intermediary.integrations == {}
        assert intermediary.watchers == {}
        assert intermediary.action_registry == {}
    
    @pytest.mark.asyncio
    async def test_notify_watchers(self):
        """Test watcher notification"""
        intermediary = Intermediary("127.0.0.1", 8765)
        
        # Add mock watcher
        mock_watcher = AsyncMock()
        intermediary.watchers["test_watcher"] = mock_watcher
        
        test_message = {"event": "test", "data": "test_data"}
        await intermediary._notify_watchers(test_message)
        
        mock_watcher.send.assert_called_once_with(json.dumps(test_message))
    
    def test_register_action(self):
        """Test action registration"""
        intermediary = Intermediary("127.0.0.1", 8765)
        
        integration_name = "test_integration"
        action_data = {
            "action_name": "test_action",
            "description": "Test action",
            "schema": {"param1": "string"}
        }
        
        intermediary.register_action(integration_name, action_data)
        
        assert integration_name in intermediary.action_registry
        assert intermediary.action_registry[integration_name]["test_action"] == action_data
    
    def test_collect_registered_actions(self):
        """Test collecting all registered actions"""
        intermediary = Intermediary("127.0.0.1", 8765)
        
        # Add some test actions
        intermediary.action_registry = {
            "integration1": {
                "action1": {"description": "Action 1"},
                "action2": {"description": "Action 2"}
            },
            "integration2": {
                "action3": {"description": "Action 3"}
            }
        }
        
        result = intermediary.collect_registered_actions()
        
        expected = {
            "action1": {"description": "Action 1"},
            "action2": {"description": "Action 2"},
            "action3": {"description": "Action 3"}
        }
        assert result == expected

class TestNakurityBackend:
    """Test NakurityBackend functionality"""
    
    @pytest.fixture
    def mock_intermediary(self):
        """Mock intermediary for testing"""
        intermediary = Mock()
        intermediary.nakurity_outbound_client = None
        intermediary._notify_watchers = AsyncMock()
        return intermediary
    
    def test_backend_initialization(self, mock_intermediary):
        """Test backend initializes correctly"""
        backend = NakurityBackend(mock_intermediary)
        assert backend.intermediary == mock_intermediary
        assert backend.clients == {}
    
    @pytest.mark.asyncio
    async def test_add_context(self, mock_intermediary):
        """Test context addition"""
        backend = NakurityBackend(mock_intermediary)
        
        game_title = "test_game"
        message = "test message"
        reply_if_not_busy = True
        
        backend.add_context(game_title, message, reply_if_not_busy)
        
        # Wait for the async task to be created
        await asyncio.sleep(0.1)
        
        # Verify intermediary was notified
        mock_intermediary._notify_watchers.assert_called()
        args = mock_intermediary._notify_watchers.call_args[0][0]
        assert args["event"] == "add_context"
        assert args["game_title"] == game_title
        assert args["message"] == message
        assert args["reply_if_not_busy"] == reply_if_not_busy
    
    @pytest.mark.asyncio
    async def test_choose_force_action(self, mock_intermediary):
        """Test forced action choice"""
        backend = NakurityBackend(mock_intermediary)
        
        # Mock actions
        mock_action1 = Mock()
        mock_action1.name = "action1"
        mock_action2 = Mock()
        mock_action2.name = "action2"
        actions = [mock_action1, mock_action2]
        
        # Test timeout scenario (no response)
        result = await backend.choose_force_action(
            game_title="test_game",
            state={},
            query="Choose action",
            ephemeral_context={},
            actions=actions
        )
        
        # Should return fallback action
        assert result[0] == "action1"
        assert result[1] == "{}"

class TestNakurityLink:
    """Test NakurityLink functionality"""
    
    @pytest.fixture
    def mock_client(self):
        """Mock NakurityClient for testing"""
        client = Mock()
        client.name = "test-client"
        client.register_actions = AsyncMock()
        client.send_command_data = AsyncMock()
        return client
    
    def test_link_initialization(self, mock_client):
        """Test link initializes correctly"""
        link = NakurityLink(mock_client)
        assert link.nakurity_client == mock_client
        assert link._bg_task is None
    
    @pytest.mark.asyncio
    async def test_register_actions(self, mock_client):
        """Test action registration"""
        link = NakurityLink(mock_client)
        
        test_actions = {
            "action1": {"description": "Test action 1"},
            "action2": {"description": "Test action 2"}
        }
        
        await link.register_actions({"actions": test_actions})
        
        # Should add to traffic queue
        assert not link.traffic.empty()
        
        # Get the traffic item
        item = await link.traffic.get()
        assert item["type"] == "register_actions"
        assert item["payload"] == test_actions
    
    @pytest.mark.asyncio
    async def test_send_event(self, mock_client):
        """Test event sending"""
        link = NakurityLink(mock_client)
        
        test_data = {
            "event": "test_event",
            "data": {"test": "data"},
            "from": "test_integration"
        }
        
        await link.send_event(test_data)
        
        # Should add to traffic queue
        assert not link.traffic.empty()
        
        # Get the traffic item
        item = await link.traffic.get()
        assert item["type"] == "event"
        assert item["event"] == "test_event"
        assert item["from"] == "test_integration"
        assert item["payload"] == {"test": "data"}

class TestIntegration:
    """Integration tests for component interactions"""
    
    @pytest.mark.asyncio
    async def test_client_to_intermediary_flow(self):
        """Test message flow from client through intermediary"""
        # Create components
        intermediary = Intermediary("127.0.0.1", 8765)
        backend = NakurityBackend(intermediary)
        
        # Mock websocket and router callback
        mock_ws = AsyncMock()
        client = NakurityClient(mock_ws, backend._handle_intermediary_forward)
        
        # Test action handling
        mock_action = Mock()
        mock_action.name = "test_action"
        mock_action.data = '{"volume": 50}'
        mock_action.id_ = "action_123"
        
        await client.handle_action(mock_action)
        
        # This should not raise any exceptions and should handle the forwarding

if __name__ == "__main__":
    # Run with: python -m pytest tests/test_relay_components.py -v
    print("Run tests with: python -m pytest tests/test_relay_components.py -v")