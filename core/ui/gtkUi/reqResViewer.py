"""
reqResViewer.py

Copyright 2008 Andres Riancho

This file is part of w3af, w3af.sourceforge.net .

w3af is free software; you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation version 2 of the License.

w3af is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with w3af; if not, write to the Free Software
Foundation, Inc., 51 Franklin St, Fifth Floor, Boston, MA  02110-1301  USA

"""
# For invoking the plugins
import threading
# Signal handler to handle SIGSEGV generated by gtkhtml2
import signal

import gtk
import gobject
import pango

from . import entries

# To show request and responses
from core.data.db.history import HistoryItem
from core.data.constants import severity
from core.controllers.w3afException import w3afException, w3afMustStopException
from core.data.parsers.httpRequestParser import httpRequestParser
from core.data.parsers.urlParser import getQueryString
from core.data.dc.queryString import queryString

import core.controllers.outputManager as om

from .export_request import export_request

# import the throbber for the audit plugin analysis
from . import helpers

# highlight
from extlib.gtkcodebuffer.gtkcodebuffer import CodeBuffer, SyntaxLoader, add_syntax_path

useMozilla = False
useGTKHtml2 = True

try:
    import gtkmozembed
    withMozilla = True
except Exception, e:
    withMozilla = False

try:
    import gtkhtml2
    #
    #   This brings crashes like:
    #       HtmlView-ERROR **: file htmlview.c: line 1906 (html_view_insert_node): assertion failed: (node->style != NULL)
    #
    #   TODO: Change this to True when gtkhtml2 is fixed
    #
    withGtkHtml2 = False
except Exception, e:
    withGtkHtml2 = False

try:
    import extlib.BeautifulSoup as BeautifulSoup
except:
    import BeautifulSoup

def sigsegv_handler(signum, frame):
    print _('This is a catched segmentation fault!')
    print _('I think you hitted bug #1933524 , this is mainly a gtkhtml2 problem. Please report this error here:')
    print _('https://sourceforge.net/tracker/index.php?func=detail&aid=1933524&group_id=170274&atid=853652')
signal.signal(signal.SIGSEGV, sigsegv_handler)
# End signal handler

class reqResViewer(gtk.VBox):
    '''
    A widget with the request and the response inside.

    @author: Andres Riancho ( andres.riancho@gmail.com )
    @author: Facundo Batista ( facundo@taniquetil.com.ar )

    '''
    def __init__(self, w3af, enableWidget=None, withManual=True, withFuzzy=True, 
                        withCompare=True, editableRequest=False, editableResponse=False, 
                        widgname="default"):
                            
        super(reqResViewer,self).__init__()
        self.w3af = w3af
        nb = gtk.Notebook()
        self.nb = nb

        self.pack_start(nb, True, True)
        nb.show()

        # Request
        self.request = requestPart(w3af, enableWidget, editable=editableRequest, widgname=widgname)
        self.request.show()
        nb.append_page(self.request, gtk.Label(_("Request")))

        # Response
        self.response = responsePart(w3af, editable=editableResponse, widgname=widgname)
        self.response.show()
        nb.append_page(self.response, gtk.Label(_("Response")))

        # Info
        self.info = searchableTextView()
        self.info.set_editable(False)
        self.info.set_border_width(5)
        #self.info.show()
        nb.append_page(self.info, gtk.Label(_("Info")))

        # Buttons
        hbox = gtk.HBox()
        
        hbox = gtk.HBox()
        
        if withManual or withFuzzy or withCompare:
            from .craftedRequests import ManualRequests, FuzzyRequests
            
            if withManual:
                b = entries.SemiStockButton("", gtk.STOCK_INDEX, _("Send Request to Manual Editor"))
                b.connect("clicked", self._sendRequest, ManualRequests)
                self.request.childButtons.append(b)
                b.show()
                hbox.pack_start(b, False, False, padding=2)
            if withFuzzy:
                b = entries.SemiStockButton("", gtk.STOCK_PROPERTIES, _("Send Request to Fuzzy Editor"))
                b.connect("clicked", self._sendRequest, FuzzyRequests)
                self.request.childButtons.append(b)
                b.show()
                hbox.pack_start(b, False, False, padding=2)
            if withCompare:
                b = entries.SemiStockButton("", gtk.STOCK_ZOOM_100, _("Send Request and Response to Compare Tool"))
                b.connect("clicked", self._sendReqResp)
                self.response.childButtons.append(b)
                b.show()
                hbox.pack_end(b, False, False, padding=2)


        # I always can export requests
        b = entries.SemiStockButton("", gtk.STOCK_COPY, _("Export Request"))
        b.connect("clicked", self._sendRequest, export_request)
        self.request.childButtons.append(b)
        b.show()
        hbox.pack_start(b, False, False, padding=2)
            
        self.pack_start(hbox, False, False, padding=5)
        hbox.show()

        # Add everything I need for the audit request thing:
        # The button that shows the menu
        b = entries.SemiStockButton("", gtk.STOCK_EXECUTE, _("Audit Request with..."))
        b.connect("button-release-event", self._popupMenu)
        self.request.childButtons.append(b)
        b.show()
        hbox.pack_start(b, False, False, padding=2)
        
        # The throbber (hidden!)
        self.throbber = helpers.Throbber()
        hbox.pack_start(self.throbber, True, True)
        
        self.pack_start(hbox, False, False, padding=5)
        hbox.show()

        self.show()

    def _popupMenu(self, widget, event):
        '''Show a Audit popup menu.'''
        _time = event.time
        # Get the information about the click
        #requestId = self._lstore[path][0]
        # Create the popup menu
        gm = gtk.Menu()
        pluginType = "audit"
        for pluginName in sorted(self.w3af.getPluginList(pluginType)):
            e = gtk.MenuItem(pluginName)
            e.connect('activate', self._auditRequest, pluginName, pluginType)
            gm.append(e)
        
        # Add a separator
        gm.append(gtk.SeparatorMenuItem())
        
        # Add a special item
        e = gtk.MenuItem('All audit plugins')
        e.connect('activate', self._auditRequest, 'All audit plugins',
                'audit_all')
        gm.append(e)
        
        # show
        gm.show_all()
        gm.popup(None, None, None, event.button, _time)

    def _auditRequest(self, menuItem, pluginName, pluginType):
        """
        Audit a request using one or more plugins.

        @parameter menuItem: The name of the audit plugin, or the 'All audit plugins' wildcard
        @parameter pluginName: The name of the plugin
        @parameter pluginType: The type of plugin
        @return: None
        """
        # We show a throbber, and start it
        self.throbber.show()
        self.throbber.running(True)
        request = self.request.getObject()
        # Now I start the analysis of this request in a new thread,
        # threading game (copied from craftedRequests)
        event = threading.Event()
        impact = ThreadedURLImpact(self.w3af, request, pluginName, pluginType, event)
        impact.start()
        gobject.timeout_add(200, self._impactDone, event, impact)

    def _impactDone(self, event, impact):
        # Keep calling this from timeout_add until isSet
        if not event.isSet():
            return True
        # We stop the throbber, and hide it
        self.throbber.hide()
        self.throbber.running(False)
        
        # Analyze the impact
        if impact.ok:
            
            #
            #   Lets check if we found any vulnerabilities
            #
            #   TODO: I should actually show ALL THE REQUESTS generated by audit plugins...
            #               not just the ones with vulnerabilities.
            #
            for result in impact.result:
                for itemId in result.getId():
                    historyItem = HistoryItem()
                    historyItem.load(itemId)
                    print 'tagging', result.plugin_name
                    historyItem.tag = result.plugin_name
                    historyItem.info = result.getDesc()
                    historyItem.save()
                    
        else:
            if impact.exception.__class__ == w3afException:
                msg = str(impact.exception)
            elif impact.exception.__class__ == w3afMustStopException:
                msg = "Stopped sending requests because " + str(impact.exception)
            else:
                raise impact.exception
            # We stop the throbber, and hide it
            self.throbber.hide()
            self.throbber.running(False)
            gtk.gdk.threads_enter()
            helpers.friendlyException(msg)
            gtk.gdk.threads_leave()
        return False

    def _sendRequest(self, widg, func):
        """Sends the texts to the manual or fuzzy request.

        @param func: where to send the request.
        """
        headers,data = self.request.getBothTexts()
        func(self.w3af, (headers,data))

    def _sendReqResp(self, widg):
        """Sends the texts to the compare tool."""
        headers,data = self.request.getBothTexts()
        self.w3af.mainwin.commCompareTool((headers, data,\
            self.response.getObject()))

    def set_sensitive(self, how):
        """Sets the pane on/off."""
        self.request.set_sensitive(how)
        self.response.set_sensitive(how)

class requestResponsePart(gtk.Notebook):
    """Request/response common class."""
    SOURCE_RAW = 1
    SOURCE_HEADERS = 2

    def __init__(self, w3af, enableWidget=None, editable=False, widgname="default"):
        super(requestResponsePart, self).__init__()
        self.def_padding = 5
        self._obj = None
        self.childButtons = []
        self._initRawTab(editable)
        self._initHeadersTab(editable)

        if enableWidget:
            self._raw.get_buffer().connect("changed", self._changed, enableWidget)
            for widg in enableWidget:
                widg(False)
        self.show()

    def _initRawTab(self, editable):
        """Init for Raw tab."""
        self._raw = searchableTextView()
        self._raw.set_editable(editable)
        self._raw.set_border_width(5)
        self._raw.show()
        self.append_page(self._raw, gtk.Label(_("Raw")))

    def _initHeadersTab(self, editable):
        """Init for Headers tab."""
        box = gtk.HBox()

        self._headersStore = gtk.ListStore(gobject.TYPE_STRING, gobject.TYPE_STRING)
        self._headersTreeview = gtk.TreeView(self._headersStore)

        # Column for Name
        renderer = gtk.CellRendererText()
        renderer.set_property('editable', editable)
        renderer.connect('edited', self._headerNameEdited, self._headersStore)
        column = gtk.TreeViewColumn(_('Name'), renderer, text=0)
        column.set_sort_column_id(0)
        column.set_resizable(True)
        self._headersTreeview.append_column(column)

        # Column for Value
        renderer = gtk.CellRendererText()
        renderer.set_property('editable', editable)
        renderer.set_property('ellipsize', pango.ELLIPSIZE_END)
        renderer.connect('edited', self._headerValueEdited, self._headersStore)
        column = gtk.TreeViewColumn(_('Value'), renderer, text=1)
        column.set_resizable(True)
        column.set_expand(True)
        column.set_sort_column_id(1)
        self._headersTreeview.append_column(column)
        self._headersTreeview.show()
        box.pack_start(self._headersTreeview)

        # Buttons area
        buttons = [
                (gtk.STOCK_GO_UP, self._moveHeaderUp),
                (gtk.STOCK_GO_DOWN, self._moveHeaderDown),
                (gtk.STOCK_ADD, self._addHeader),
                (gtk.STOCK_DELETE, self._deleteHeader)
                ]

        buttonBox = gtk.VBox()

        for button in buttons:
            b = gtk.Button(stock=button[0])
            b.connect("clicked", button[1])
            b.show()
            buttonBox.pack_start(b, False, False, self.def_padding)
        buttonBox.show()

        if editable:
            box.pack_start(buttonBox, False, False, self.def_padding)
        box.show()
        self.append_page(box, gtk.Label(_("Headers")))

    def _addHeader(self, widget):
        """Add header to header."""
        i = self._headersStore.append(["", ""])
        selection = self._headersTreeview.get_selection()
        selection.select_iter(i)

    def _deleteHeader(self, widget):
        """Delete selected header."""
        selection = self._headersTreeview.get_selection()
        (model, selected) = selection.get_selected()
        if selected:
            model.remove(selected)
        self._changeHeaderCB()
        self._synchronize(self.SOURCE_HEADERS)

    def _moveHeaderDown(self, widget):
        """Move down selected header."""
        selection = self._headersTreeview.get_selection()
        (model, selected) = selection.get_selected()
        if not selected:
            return
        next = model.iter_next(selected)
        if next:
            model.swap(selected, next)
        self._changeHeaderCB()
        self._synchronize(self.SOURCE_HEADERS)

    def _moveHeaderUp(self, widget):
        """Move up selected header."""
        selection = self._headersTreeview.get_selection()
        model, selected = selection.get_selected()
        if not selected:
            return
        path = model.get_path(selected)
        position = path[-1]
        if position == 0:
            return
        prev_path = list(path)[:-1]
        prev_path.append(position - 1)
        prev = model.get_iter(tuple(prev_path))
        model.swap(selected, prev)
        self._changeHeaderCB()
        self._synchronize(self.SOURCE_HEADERS)

    def _headerNameEdited(self, cell, path, new_text, model):
        model[path][0] = new_text
        self._changeHeaderCB()
        self._synchronize(self.SOURCE_HEADERS)

    def _headerValueEdited(self, cell, path, new_text, model):
        model[path][1] = new_text
        self._changeHeaderCB()
        self._synchronize(self.SOURCE_HEADERS)

    def set_sensitive(self, how):
        """Sets the pane on/off."""
        super(requestResponsePart, self).set_sensitive(how)
        for but in self.childButtons:
            but.set_sensitive(how)

    def _changed(self, widg, toenable):
        """Supervises if the widget has some text."""
        rawBuf = self._raw.get_buffer()
        rawText = rawBuf.get_text(rawBuf.get_start_iter(), rawBuf.get_end_iter())

        for widg in toenable:
            widg(bool(rawText))

        self._changeRawCB()
        self._synchronize(self.SOURCE_RAW)

    def _clear(self, textView):
        """Clears a text view."""
        buff = textView.get_buffer()
        start, end = buff.get_bounds()
        buff.delete(start, end)

    def clearPanes(self):
        """Public interface to clear both panes."""
        self._clear(self._raw)

    def showError(self, text):
        """Show an error.
        Errors are shown in the upper part, with the lower one greyed out.
        """
        self._clear(self._raw)
        buff = self._raw.get_buffer()
        iter = buff.get_end_iter()
        buff.insert(iter, text)

    def getBothTexts(self):
        """Returns request data as turple headers + data."""
        rawBuf = self._raw.get_buffer()
        rawText = rawBuf.get_text(rawBuf.get_start_iter(), rawBuf.get_end_iter())
        headers = rawText
        data = ""
        tmp = rawText.find("\n\n")

        # It's POST!
        if tmp != -1:
            headers = rawText[0:tmp+1]
            data = rawText[tmp+2:]
            if data.strip() == "":
                data = ""
        return (headers, data)

    def _to_utf8(self, text):
        """
        This method was added to fix:

        GtkWarning: gtk_text_buffer_emit_insert: assertion `g_utf8_validate (text, len, NULL)'

        @parameter text: A text that may or may not be in UTF-8.
        @return: A text, that's in UTF-8, and can be printed in a text view
        """
        text = repr(text)
        text = text[1:-1]

        for special_char in ['\n', '\r', '\t']:
            text = text.replace( repr(special_char)[1:-1], special_char )
            
        text = text.replace("\\'", "'")
        text = text.replace('\\\\"', '\\"')
        
        return text

    def showObject(self, obj):
        raise w3afException('Child MUST implment a showObject method.')

    def getObject(self):
        return self._obj

    def _synchronize(self):
        raise w3afException('Child MUST implment a _synchronize method.')

    def _changeHeaderCB(self):
        raise w3afException('Child MUST implment a _changeHeaderCB method.')

    def _changeRawCB(self):
        raise w3afException('Child MUST implment a _changeRawCB method.')

    def _updateHeadersTab(self, headers):
        self._headersStore.clear()
        for header in headers:
            self._headersStore.append([header, headers[header]])

    def getRawTextView(self):
        return self._raw

    def highlight(self, text, sev=severity.MEDIUM):
        """Find the text, and handle highlight.
        @return: None
        """
        # highlight the response header and body
        for text_buffer in [self._raw]:
            (ini, fin) = text_buffer.get_bounds()
            alltext = text_buffer.get_text(ini, fin)
            # find the positions where the phrase is found
            positions = []
            pos = 0
            while True:
                try:
                    pos = alltext.index(text, pos)
                except ValueError:
                    break
                fin = pos + len(text)
                iterini = text_buffer.get_iter_at_offset(pos)
                iterfin = text_buffer.get_iter_at_offset(fin)
                positions.append((pos, fin, iterini, iterfin))
                pos += 1
            # highlight them all
            for (ini, fin, iterini, iterfin) in positions:
                text_buffer.apply_tag_by_name(sev, iterini, iterfin)

class requestPart(requestResponsePart):
    """Request part"""

    def __init__(self, w3af, enableWidget=None, editable=False, widgname="default"):
        requestResponsePart.__init__(self, w3af, enableWidget, editable, widgname=widgname+"request")
        self.SOURCE_PARAMS = 3
        self._initParamsTab(editable)
        self.show()

    def _initParamsTab(self, editable):
        """Init Params tab."""
        box = gtk.HBox()
        self._paramsStore = gtk.ListStore(gobject.TYPE_STRING, gobject.TYPE_STRING)
        self._paramsTreeview = gtk.TreeView(self._paramsStore)
        # Column for Name
        renderer = gtk.CellRendererText()
        renderer.set_property('editable', editable)
        renderer.connect('edited', self._paramNameEdited, self._paramsStore)
        column = gtk.TreeViewColumn(_('Name'), renderer, text=0)
        column.set_sort_column_id(0)
        column.set_resizable(True)
        self._paramsTreeview.append_column(column)
        # Column for Value
        renderer = gtk.CellRendererText()
        renderer.set_property('editable', editable)
        renderer.set_property('ellipsize', pango.ELLIPSIZE_END)
        renderer.connect('edited', self._paramValueEdited, self._paramsStore)
        column = gtk.TreeViewColumn(_('Value'), renderer, text=1)
        column.set_resizable(True)
        column.set_expand(True)
        column.set_sort_column_id(1)
        self._paramsTreeview.append_column(column)
        self._paramsTreeview.show()
        box.pack_start(self._paramsTreeview)

        # Buttons area
        buttons = [
                (gtk.STOCK_ADD, self._addParam),
                (gtk.STOCK_DELETE, self._deleteParam)
                ]

        buttonBox = gtk.VBox()

        for button in buttons:
            b = gtk.Button(stock=button[0])
            b.connect("clicked", button[1])
            b.show()
            buttonBox.pack_start(b, False, False, self.def_padding)
        buttonBox.show()

        if editable:
            box.pack_start(buttonBox, False, False, self.def_padding)
        box.show()
        self.append_page(box, gtk.Label(_("Params")))

    def _addParam(self, widget):
        """Add param to params table."""
        i = self._paramsStore.append(["", ""])
        selection = self._headersTreeview.get_selection()
        selection.select_iter(i)

    def _deleteParam(self, widget):
        """Delete selected param."""
        selection = self._paramsTreeview.get_selection()
        (model, selected) = selection.get_selected()
        if selected:
            model.remove(selected)
        self._changeParamCB()
        self._synchronize(self.SOURCE_PARAMS)

    def _paramNameEdited(self, cell, path, new_text, model):
        model[path][0] = new_text
        self._changeParamCB()
        self._synchronize(self.SOURCE_PARAMS)

    def _paramValueEdited(self, cell, path, new_text, model):
        model[path][1] = new_text
        self._changeParamCB()
        self._synchronize(self.SOURCE_PARAMS)

    def showObject(self, fuzzableRequest):
        """Show the data from a fuzzableRequest object in the textViews."""
        self._obj = fuzzableRequest
        self._synchronize()

    def showRaw(self, head, body):
        """Show the raw data."""
        self._obj = httpRequestParser(head, body)
        self._synchronize()

    def _synchronize(self, source=None):
        # Raw tab
        if source != self.SOURCE_RAW:
            self._clear(self._raw)
            buff = self._raw.get_buffer()
            buff.set_text(self._to_utf8(self._obj.dump()))
        # Headers tab
        if source != self.SOURCE_HEADERS:
            self._updateHeadersTab(self._obj.getHeaders())
        # Params tab
        if source != self.SOURCE_PARAMS:
            queryParams = getQueryString(self._obj.getURI())
            self._updateParamsTab(queryParams)

    def _updateParamsTab(self, queryParams):
        self._paramsStore.clear()
        for paramName in queryParams:
            if isinstance(queryParams[paramName], list):
                for dubParamValue in queryParams[paramName]:
                    self._paramsStore.append([paramName, dubParamValue])
            else:
                self._paramsStore.append([paramName, queryParams[paramName]])

    def _changeParamCB(self):
        rQueryString  = queryString()
        for param in self._paramsStore:
            if param[0] in rQueryString:
                rQueryString[param[0]].append(param[1])
            else:
                rQueryString[param[0]] = [param[1], ]
        url = self._obj.getURL()
        self._obj.setURI(url + "?" + str(rQueryString))

    def _changeHeaderCB(self):
        headers = {}
        # TODO Add Cookie processing?!
        for header in self._headersStore:
            headers[header[0]] = header[1]
        self._obj.setHeaders(headers)

    def _changeRawCB(self):
        (head, data) = self.getBothTexts()
        try:
            if not len(head):
                raise w3afException("Empty HTTP Request head")
            self._obj = httpRequestParser(head, data)
            self._raw.reset_bg_color()
        except w3afException, ex:
            self._raw.set_bg_color(gtk.gdk.color_parse("#FFCACA"))

class responsePart(requestResponsePart):
    """Response part"""

    def __init__(self, w3af, editable=False, widgname="default"):
        requestResponsePart.__init__(self, w3af, editable=editable, widgname=widgname+"response")
        # Second page, only there if html renderer available
        self._initRenderTab()
        # Third page, only if the content is some type of markup language (xml, html)
        self._initSyntaxTab()
        self.show()

    def _initSyntaxTab(self):
        """Init Syntax Tab."""
        self._markup_highlight = None
        #if is_markup():
        lang = SyntaxLoader("xml")
        self._markup_highlight_buff = CodeBuffer(lang=lang)
        self._markup_highlight = gtk.ScrolledWindow()
        self._markup_highlight.add( gtk.TextView(self._markup_highlight_buff) )
        self._markup_highlight.show_all()
        self.append_page(self._markup_highlight, gtk.Label(_("HTML")))

    def _initRenderTab(self):
        """Init Render Tab."""
        self._renderingWidget = None

        if not withMozilla and not withGtkHtml2:
            return

        if withGtkHtml2 and useGTKHtml2:
            renderWidget = gtkhtml2.View()
            self._renderFunction = self._renderGtkHtml2
        elif withMozilla and useMozilla:
            renderWidget = gtkmozembed.MozEmbed()
            self._renderFunction = self._renderMozilla
        else:
            renderWidget = None

        self._renderingWidget = renderWidget
        if renderWidget is not None:
            swRenderedHTML = gtk.ScrolledWindow()
            swRenderedHTML.add(renderWidget)
            swRenderedHTML.show_all()
            self.append_page(swRenderedHTML, gtk.Label(_("Rendered")))

    def showObject(self, httpResp):
        """Show the data from a httpResp object."""
        self._obj = httpResp
        self._synchronize()

    def _synchronize(self, source=None):
        # Raw tab
        self._clear(self._raw)
        buff = self._raw.get_buffer()
        buff.set_text(self._to_utf8(self._obj.dump()))
        # Headers tab
        self._updateHeadersTab(self._obj.getHeaders())
        # Render
        self._showParsed("1.1", self._obj.getCode(), self._obj.getMsg(),\
                self._obj.dumpResponseHead(), self._obj.getBody(), self._obj.getURI())
        # Syntax highlighting
        self._showSyntax(self._obj.getBody())

    def _changeHeaderCB(self):
        pass

    def _changeRawCB(self):
        pass

    def _renderGtkHtml2(self, body, mimeType, baseURI):
        # It doesn't make sense to render something empty
        if body == '':
            return

        try:
            document = gtkhtml2.Document()
            document.clear()
            document.open_stream(mimeType)
            document.write_stream(body)
            document.close_stream()
            self._renderingWidget.set_document(document)
        except ValueError, ve:
            # I get here when the mime type is an image or something that I can't display
            pass
        except Exception, e:
            print _('This is a catched exception!')
            print _('Exception:'), type(e), str(e)
            print _('I think you hitted bug #1933524 , this is mainly a gtkhtml2 problem. Please report this error here:')
            print _('https://sourceforge.net/tracker/index.php?func=detail&aid=1933524&group_id=170274&atid=853652')

    def _renderMozilla(self, body, mimeType, baseURI):
        self._renderingWidget.render_data(body, long(len(body)), baseURI , mimeType)

    def _showSyntax(self, body):
        self._markup_highlight_buff.set_text(self._obj.getBody())

    def _showParsed(self, version, code, msg, headers, body, baseURI):
        """Show the parsed data"""
        if self._renderingWidget is None:
            return
        # Clear previous results
        #self._clear( self._raw )

        #buff = self._raw.get_buffer()
        #iterl = buff.get_end_iter()
        #buff.insert( iterl, 'HTTP/' + version + ' ' + str(code) + ' ' + str(msg) + '\n')
        #buff.insert( iterl, headers )
        
        # Get the mimeType from the response headers
        mimeType = 'text/html'
        #headers = headers.split('\n')
        #headers = [h for h in headers if h]
        #for h in headers:
        #    h_name, h_value = h.split(':', 1)
        #    if 'content-type' in h_name.lower():
        #        mimeType = h_value.strip()
        #        break
        # FIXME: Show images
        if 'image' in mimeType:
            mimeType = 'text/html'
            body = _('The response type is: <i>') + mimeType + _('</i>. w3af is still under development, in the future images will be displayed.')

        # Show it rendered, but before rendering, use BeautifulSoup to normalize the HTML
        # this should avoid some bugs in the HTML renderers!
        soup = BeautifulSoup.BeautifulSoup(body)
        body = soup.prettify()
       
        self._renderFunction(body, mimeType, baseURI)



SEVERITY_TO_COLOR={
    severity.INFORMATION: 'green', 
    severity.LOW: 'blue',
    severity.MEDIUM: 'yellow',
    severity.HIGH: 'red'}
SEVERITY_TO_COLOR.setdefault('yellow')

class searchableTextView(gtk.VBox, entries.Searchable):
    """A textview widget that supports searches.

    @author: Andres Riancho ( andres.riancho@gmail.com )
    """
    def __init__(self):
        gtk.VBox.__init__(self)

        # Create the textview where the text is going to be shown
        self.textView = gtk.TextView()
        self.textView.set_wrap_mode(gtk.WRAP_WORD)

        self.reset_bg_color()
        for sev in SEVERITY_TO_COLOR:
            self.textView.get_buffer().create_tag(sev, background=SEVERITY_TO_COLOR[sev])
        self.textView.show()

        # Scroll where the textView goes
        sw1 = gtk.ScrolledWindow()
        sw1.set_shadow_type(gtk.SHADOW_ETCHED_IN)
        sw1.set_policy(gtk.POLICY_AUTOMATIC, gtk.POLICY_AUTOMATIC)
        sw1.add(self.textView)
        sw1.show()
        self.pack_start(sw1, expand=True, fill=True)

        # Create the search widget
        entries.Searchable.__init__(self, self.textView, small=True)

    def get_bounds(self):
        return self.textView.get_buffer().get_bounds()

    def get_text(self, start,  end):
        return self.textView.get_buffer().get_text(start, end)

    def get_iter_at_offset(self, position):
        return self.textView.get_buffer().get_iter_at_offset(position)

    def apply_tag_by_name(self, tag, start, end):
        return self.textView.get_buffer().apply_tag_by_name(tag, start, end)

    def set_editable(self, e):
        return self.textView.set_editable(e)

    def set_border_width(self, b):
        return self.textView.set_border_width(b)

    def set_bg_color(self, color):
        self.textView.modify_base(gtk.STATE_NORMAL, color)

    def reset_bg_color(self):
        self.textView.modify_base(gtk.STATE_NORMAL, gtk.gdk.color_parse("#FFFFFF"))

    def get_buffer(self):
        return self.textView.get_buffer()

class reqResWindow(entries.RememberingWindow):
    """
    A window to show a request/response pair.
    """
    def __init__(self, w3af, request_id, enableWidget=None, withManual=True,
                 withFuzzy=True, withCompare=True, editableRequest=False,
                 editableResponse=False, widgname="default"):
        # Create the window
        entries.RememberingWindow.__init__(
            self, w3af, "reqResWin", _("w3af - HTTP Request/Response"), "Browsing_the_Knowledge_Base")

        # Create the request response viewer
        rrViewer = reqResViewer(w3af, enableWidget, withManual, withFuzzy, withCompare, editableRequest, editableResponse, widgname)

        # Search the id in the DB
        historyItem = HistoryItem()
        historyItem.load(request_id)
        # Set
        rrViewer.request.showObject( historyItem.request )
        rrViewer.response.showObject( historyItem.response )
        rrViewer.show()
        self.vbox.pack_start(rrViewer)

        # Show the window
        self.show()

class ThreadedURLImpact(threading.Thread):
    '''Impacts an URL in a different thread.'''
    def __init__(self, w3af, request, pluginName, pluginType, event):
        '''Init ThreadedURLImpact.'''
        self.w3af = w3af
        self.request = request
        self.pluginName = pluginName
        self.pluginType = pluginType
        self.event = event
        self.result = []
        self.ok = False
        threading.Thread.__init__(self)

    def run(self):
        '''Start the thread.'''
        try:
            # First, we check if the user choosed 'All audit plugins'
            if self.pluginType == 'audit_all':
                
                #
                #   Get all the plugins and work with that list
                #
                for pluginName in self.w3af.getPluginList('audit'):
                    plugin = self.w3af.getPluginInstance(pluginName, 'audit')
                    tmp_result = []
                    try:
                        tmp_result = plugin.audit_wrapper(self.request)
                        plugin.end()
                    except w3afException, e:
                        om.out.error(str(e))
                    else:
                        #
                        #   Save the plugin that found the vulnerability in the result
                        #
                        for r in tmp_result:
                            r.plugin_name = pluginName
                        self.result.extend(tmp_result)

                
            else:
                #
                #   Only one plugin was enabled
                #
                plugin = self.w3af.getPluginInstance(self.pluginName, self.pluginType)
                try:
                    self.result = plugin.audit_wrapper(self.request)
                    plugin.end()
                except w3afException, e:
                    om.out.error(str(e))
                else:
                    #
                    #   Save the plugin that found the vulnerability in the result
                    #
                    for r in self.result:
                        r.plugin_name = self.pluginName
            
            #   We got here, everything is OK!
            self.ok = True
            
        except Exception, e:
            self.exception = e
            #
            #   This is for debugging errors in the audit button of the reqResViewer
            #
            #import traceback
            #print traceback.format_exc()
        finally:
            self.event.set()
