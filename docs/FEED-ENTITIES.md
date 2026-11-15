# Feed entities (ERP fields as AIDW serves them)

The nine entity sets `app.sync.acumatica.run_sync` reads from the AIDW OData feed, with every field
the feed returns and its JSON type in the sample (`null` when every sampled row had no value, `float`
when the sampled values mix whole and fractional numbers). Generated 2026-11-13 from the QA feed, 200
rows per entity set (field names only, never values). **Write acceptance criteria and code against
these names**: a field that is not listed here does not exist in the feed (for example `POLine` has
the booleans `Completed` and `Cancelled` and **no** `Status` field).

`BAccount`, `SOLine` and `POVendorInventory` are read after the first six. A feed that does not expose
one of them skips that read model (suppliers, open sales lines or supplier terms) instead of failing
the sync.

Fields marked **used** are read by the sync today.

## InventoryItem

| field | type | used |
|---|---|---|
| `ABCCodeID` | null |  |
| `ABCCodeIsFixed` | bool |  |
| `AMBOMID` | str |  |
| `AMCTPItem` | bool |  |
| `AMCheckSchdMatlAvailability` | bool |  |
| `AMConfigurationID` | str |  |
| `AMDefaultMarkFor` | int |  |
| `AMGroupWindow` | int |  |
| `AMGroupWindowOverride` | bool |  |
| `AMLotSize` | float |  |
| `AMLowLevel` | int |  |
| `AMMFGLeadTime` | int |  |
| `AMMakeToOrderItem` | bool |  |
| `AMMaxOrdQty` | float |  |
| `AMMinOrdQty` | float |  |
| `AMMinQty` | float |  |
| `AMPlanningBOMID` | null |  |
| `AMQtyRoundUp` | bool |  |
| `AMSafetyStock` | float |  |
| `AMWIPAcctID` | null |  |
| `AMWIPSubID` | null |  |
| `AMWIPVarianceAcctID` | null |  |
| `AMWIPVarianceSubID` | null |  |
| `ActionType` | str |  |
| `AttributeDescriptionGroupID` | null |  |
| `Availability` | str |  |
| `AvailabilityAdjustment` | float |  |
| `BaseItemVolume` | float |  |
| `BaseItemWeight` | float |  |
| `BasePrice` | float |  |
| `BasePriceDate` | null |  |
| `BaseUnit` | str | **used** |
| `BaseVolume` | float |  |
| `BaseWeight` | float |  |
| `BenefitExpenseAcctID` | null |  |
| `BenefitExpenseSubID` | null |  |
| `BillingRule` | str |  |
| `Body` | str |  |
| `COGSAcctID` | int |  |
| `COGSSubID` | int |  |
| `ColumnAttributeValue` | null |  |
| `Commisionable` | bool |  |
| `CommodityCodeType` | null |  |
| `CompletePOLine` | str |  |
| `CostBasis` | str |  |
| `CountryOfOrigin` | str |  |
| `CpnyWarrantyType` | str |  |
| `CpnyWarrantyValue` | int |  |
| `CreatedByID` | str |  |
| `CreatedByScreenID` | str |  |
| `CreatedDateTime` | str |  |
| `CycleID` | null |  |
| `DecimalBaseUnit` | bool |  |
| `DecimalPurchaseUnit` | bool |  |
| `DecimalSalesUnit` | bool |  |
| `DefaultColumnMatrixAttributeID` | null |  |
| `DefaultRowMatrixAttributeID` | null |  |
| `DefaultSubItemOnEntry` | bool |  |
| `DefaultTerm` | float |  |
| `DefaultTermUOM` | str |  |
| `DeferralAcctID` | null |  |
| `DeferralSubID` | null |  |
| `DeferredCode` | str |  |
| `DeletedDatabaseRecord` | bool |  |
| `Descr` | str | **used** |
| `DfltEarningType` | str |  |
| `DfltReceiptLocationID` | int |  |
| `DfltShipLocationID` | int |  |
| `DfltSiteID` | int |  |
| `DiscAcctID` | null |  |
| `DiscSubID` | null |  |
| `EQEnabled` | bool |  |
| `EarningsAcctID` | null |  |
| `EarningsSubID` | null |  |
| `EntityTypeID` | null |  |
| `EquipmentItemClass` | str |  |
| `EquipmentTypeID` | null |  |
| `EstimatedDuration` | int |  |
| `ExpenseAccrualAcctID` | null |  |
| `ExpenseAccrualSubID` | null |  |
| `ExpenseAcctID` | null |  |
| `ExpenseSubID` | null |  |
| `ExportToExternal` | bool |  |
| `GenerationRuleCntr` | int |  |
| `HSTariffCode` | null |  |
| `HasChild` | null |  |
| `ImageUrl` | str |  |
| `Included` | bool |  |
| `InventoryCD` | str | **used** |
| `InventoryID` | int | **used** |
| `InvtAcctID` | int |  |
| `InvtSubID` | int |  |
| `IsConversionMode` | null |  |
| `IsConverted` | bool |  |
| `IsSplitted` | bool |  |
| `IsTemplate` | bool |  |
| `IsTravelItem` | bool |  |
| `ItemClassID` | int |  |
| `ItemStatus` | str |  |
| `ItemType` | str |  |
| `KitItem` | bool |  |
| `LCVarianceAcctID` | int |  |
| `LCVarianceSubID` | int |  |
| `LastBasePrice` | null |  |
| `LastModifiedByID` | str |  |
| `LastModifiedByScreenID` | str |  |
| `LastModifiedDateTime` | str |  |
| `LastSiteID` | null |  |
| `LastStdCost` | float |  |
| `LotSerClassID` | str |  |
| `ManufacturerID` | null |  |
| `ManufacturerModelID` | null |  |
| `MarkupPct` | float |  |
| `MinGrossProfitPct` | float |  |
| `ModelType` | str |  |
| `MovementClassID` | null |  |
| `MovementClassIsFixed` | bool |  |
| `NegQty` | bool |  |
| `NonStockReceipt` | bool |  |
| `NonStockReceiptAsService` | bool |  |
| `NonStockShip` | bool |  |
| `NotAvailMode` | str |  |
| `NoteID` | str |  |
| `NotePopupText` | str |  |
| `NoteText` | str |  |
| `OrigLotSerClassID` | str |  |
| `OvershipThreshold` | float |  |
| `POAccrualAcctID` | int |  |
| `POAccrualSubID` | int |  |
| `PPVAcctID` | int |  |
| `PPVSubID` | int |  |
| `PTOExpenseSubID` | null |  |
| `PackSeparately` | bool |  |
| `PackageOption` | str |  |
| `ParentItemClassID` | int |  |
| `PendingBasePrice` | null |  |
| `PendingBasePriceDate` | null |  |
| `PendingStdCost` | float |  |
| `PendingStdCostDate` | null |  |
| `PercentOfSalesPrice` | float |  |
| `PlanningMethod` | str |  |
| `PostClassID` | str |  |
| `PostToExpenseAccount` | str |  |
| `PreferredItemClasses` | str |  |
| `PreferredVendorID` | int |  |
| `PreferredVendorLocationID` | int |  |
| `PriceClassID` | str |  |
| `PriceManagerID` | null |  |
| `PriceOfSuggestedItems` | str |  |
| `PriceWorkgroupID` | null |  |
| `ProductManagerID` | null |  |
| `ProductWorkgroupID` | null |  |
| `PurchaseUnit` | str |  |
| `ReasonCodeSubID` | int |  |
| `RecPrice` | float |  |
| `ReplenishmentSource` | str |  |
| `RowAttributeValue` | null |  |
| `SOSource` | str |  |
| `SalesAcctID` | int |  |
| `SalesSubID` | int |  |
| `SalesUnit` | str |  |
| `SampleDescription` | null |  |
| `SampleID` | null |  |
| `Secured` | null |  |
| `StdCost` | float |  |
| `StdCostDate` | str |  |
| `StdCstRevAcctID` | int |  |
| `StdCstRevSubID` | int |  |
| `StdCstVarAcctID` | int |  |
| `StdCstVarSubID` | int |  |
| `StkItem` | bool | **used** |
| `TaxCalcMode` | str |  |
| `TaxCategoryID` | str |  |
| `TaxExpenseAcctID` | null |  |
| `TaxExpenseSubID` | null |  |
| `TemplateItemID` | null |  |
| `TotalPercentage` | null |  |
| `UndershipThreshold` | float |  |
| `UpdateOnlySelected` | bool |  |
| `UseParentSubID` | bool |  |
| `ValMethod` | str |  |
| `VendorWarrantyType` | str |  |
| `VendorWarrantyValue` | int |  |
| `Visibility` | str |  |
| `VolumeUOM` | str |  |
| `WeightItem` | bool |  |
| `WeightUOM` | str |  |
| `business_key` | str |  |
| `chkEquipmentManagement` | bool |  |
| `chkServiceManagement` | bool |  |
| `tstamp` | str |  |

## INItemSite

| field | type | used |
|---|---|---|
| `ABCCodeID` | null |  |
| `ABCCodeIsFixed` | bool |  |
| `ABCCodeOverride` | bool |  |
| `AMBOMID` | str |  |
| `AMConfigurationID` | str |  |
| `AMGroupWindow` | int |  |
| `AMGroupWindowOverride` | bool |  |
| `AMLotSize` | float |  |
| `AMLotSizeOverride` | bool |  |
| `AMMFGLeadTime` | int |  |
| `AMMFGLeadTimeOverride` | bool |  |
| `AMMaxOrdQty` | float |  |
| `AMMaxOrdQtyOverride` | bool |  |
| `AMMinOrdQty` | float |  |
| `AMMinOrdQtyOverride` | bool |  |
| `AMMinQty` | null |  |
| `AMMinQtyOverride` | bool |  |
| `AMPlanningBOMID` | null |  |
| `AMReplenishmentSource` | str |  |
| `AMReplenishmentSourceOverride` | bool |  |
| `AMSafetyStock` | null |  |
| `AMSafetyStockOverride` | bool |  |
| `AMScrapLocationID` | null |  |
| `AMScrapOverride` | bool |  |
| `AMScrapSiteID` | null |  |
| `AMSourceSiteID` | int |  |
| `AMSourceSiteIDOverride` | bool |  |
| `AMTransferLeadTime` | int |  |
| `AMTransferLeadTimeOverride` | bool |  |
| `Active` | bool |  |
| `AutoFitModel` | bool |  |
| `AvgCost` | float | **used** |
| `BasePrice` | float |  |
| `BasePriceDate` | null |  |
| `BasePriceOverride` | bool |  |
| `Commissionable` | bool |  |
| `CountryOfOrigin` | str |  |
| `CreatedByID` | str |  |
| `CreatedByScreenID` | str |  |
| `CreatedDateTime` | str |  |
| `DemandPerDayAverage` | null |  |
| `DemandPerDayMAD` | null |  |
| `DemandPerDayMSE` | null |  |
| `DemandPerDaySTDEV` | null |  |
| `DfltPurchaseUnit` | str |  |
| `DfltPutawayLocationID` | int |  |
| `DfltReceiptLocationID` | int |  |
| `DfltSalesUnit` | str |  |
| `DfltShipLocationID` | int |  |
| `ESSmoothingConstantL` | null |  |
| `ESSmoothingConstantLOverride` | bool |  |
| `ESSmoothingConstantS` | null |  |
| `ESSmoothingConstantSOverride` | bool |  |
| `ESSmoothingConstantT` | null |  |
| `ESSmoothingConstantTOverride` | bool |  |
| `ForecastModelType` | null |  |
| `ForecastPeriodType` | null |  |
| `InventoryID` | int | **used** |
| `InvtAcctID` | null |  |
| `InvtSubID` | null |  |
| `IsDefault` | null |  |
| `LastBasePrice` | float |  |
| `LastCost` | float | **used** |
| `LastCostDate` | null |  |
| `LastFCApplicationDate` | null |  |
| `LastForecastDate` | null |  |
| `LastModifiedByID` | str |  |
| `LastModifiedByScreenID` | str |  |
| `LastModifiedDateTime` | str |  |
| `LastStdCost` | float |  |
| `LaunchDate` | str |  |
| `LaunchDateOverride` | bool |  |
| `LeadTimeAverage` | null |  |
| `LeadTimeMSE` | null |  |
| `LeadTimeSTDEV` | null |  |
| `MarkupPct` | float |  |
| `MarkupPctOverride` | bool |  |
| `MaxCost` | float |  |
| `MaxQty` | float | **used** |
| `MaxQtyOverride` | bool |  |
| `MaxQtySuggested` | null |  |
| `MaxShelfLife` | int |  |
| `MaxShelfLifeOverride` | bool |  |
| `MinCost` | float |  |
| `MinQty` | float | **used** |
| `MinQtyOverride` | bool |  |
| `MinQtySuggested` | float |  |
| `MovementClassID` | null |  |
| `MovementClassIsFixed` | bool |  |
| `MovementClassOverride` | bool |  |
| `NoteID` | str |  |
| `NoteText` | str |  |
| `OverrideInvtAcctSub` | bool |  |
| `POCreate` | bool |  |
| `POSource` | str |  |
| `PendingBasePrice` | float |  |
| `PendingBasePriceDate` | null |  |
| `PendingMovementClassID` | null |  |
| `PendingMovementClassPeriodID` | null |  |
| `PendingMovementClassUpdateDate` | null |  |
| `PendingStdCost` | float |  |
| `PendingStdCostDate` | null |  |
| `PendingStdCostReset` | bool |  |
| `PlanningMethod` | str |  |
| `PreferredVendorID` | int |  |
| `PreferredVendorLocationID` | int |  |
| `PreferredVendorOverride` | bool |  |
| `PriceManagerID` | null |  |
| `PriceWorkgroupID` | null |  |
| `ProductManagerID` | null |  |
| `ProductManagerOverride` | bool |  |
| `ProductWorkgroupID` | null |  |
| `RecPrice` | float |  |
| `RecPriceOverride` | bool |  |
| `ReplenishmentClassID` | str |  |
| `ReplenishmentMethod` | str |  |
| `ReplenishmentPolicyID` | str |  |
| `ReplenishmentPolicyOverride` | bool |  |
| `ReplenishmentSource` | str |  |
| `ReplenishmentSourceSiteID` | int |  |
| `SafetyStock` | float | **used** |
| `SafetyStockOverride` | bool |  |
| `SafetyStockSuggested` | float |  |
| `ServiceLevel` | float |  |
| `ServiceLevelOverride` | bool |  |
| `ServiceLevelPct` | float |  |
| `SiteID` | int | **used** |
| `SiteStatus` | str |  |
| `StdCost` | float | **used** |
| `StdCostDate` | str |  |
| `StdCostOverride` | bool |  |
| `TerminationDate` | null |  |
| `TerminationDateOverride` | bool |  |
| `TranUnitCost` | float |  |
| `TransferERQ` | float |  |
| `TransferERQOverride` | bool |  |
| `ValMethod` | str |  |
| `business_key` | str |  |
| `tstamp` | str |  |

## INSiteStatus

| field | type | used |
|---|---|---|
| `InventoryID` | int | **used** |
| `LastModifiedByScreenID` | str |  |
| `LastModifiedDateTime` | str |  |
| `QtyActual` | float |  |
| `QtyAvail` | float |  |
| `QtyExpired` | float |  |
| `QtyFSSrvOrdAllocated` | float |  |
| `QtyFSSrvOrdBooked` | float |  |
| `QtyFSSrvOrdPrepared` | float |  |
| `QtyFixedFSSrvOrd` | float |  |
| `QtyHardAvail` | float |  |
| `QtyINAssemblyDemand` | float |  |
| `QtyINAssemblySupply` | float |  |
| `QtyINIssues` | float |  |
| `QtyINReceipts` | float |  |
| `QtyINReplaned` | float |  |
| `QtyInTransit` | float |  |
| `QtyInTransitToProduction` | float |  |
| `QtyInTransitToSO` | float |  |
| `QtyMLAllocated` | float |  |
| `QtyMLBooked` | float |  |
| `QtyMLDispatched` | float |  |
| `QtyMLPrepared` | float |  |
| `QtyMLToPurchase` | float |  |
| `QtyNotAvail` | float |  |
| `QtyOnHand` | float | **used** |
| `QtyPODropShipOrders` | float |  |
| `QtyPODropShipPrepared` | float |  |
| `QtyPODropShipReceipts` | float |  |
| `QtyPOFixedFSSrvOrd` | float |  |
| `QtyPOFixedFSSrvOrdPrepared` | float |  |
| `QtyPOFixedFSSrvOrdReceipts` | float |  |
| `QtyPOFixedOrders` | float |  |
| `QtyPOFixedPrepared` | float |  |
| `QtyPOFixedProductionOrders` | float |  |
| `QtyPOFixedProductionPrepared` | float |  |
| `QtyPOFixedReceipts` | float |  |
| `QtyPOOrders` | float |  |
| `QtyPOPrepared` | float |  |
| `QtyPOReceipts` | float |  |
| `QtyProdFixedProdOrders` | float |  |
| `QtyProdFixedProdOrdersPrepared` | float |  |
| `QtyProdFixedProduction` | float |  |
| `QtyProdFixedPurchase` | float |  |
| `QtyProdFixedSalesOrders` | float |  |
| `QtyProdFixedSalesOrdersPrepared` | float |  |
| `QtyProductionAllocated` | float |  |
| `QtyProductionDemand` | float |  |
| `QtyProductionDemandPrepared` | float |  |
| `QtyProductionSupply` | float |  |
| `QtyProductionSupplyPrepared` | float |  |
| `QtyPurchaseForML` | float |  |
| `QtyPurchaseForMLPrepared` | float |  |
| `QtyReceiptsForML` | float |  |
| `QtySOBackOrdered` | float |  |
| `QtySOBooked` | float |  |
| `QtySODropShip` | float |  |
| `QtySOFixed` | float |  |
| `QtySOFixedProduction` | float |  |
| `QtySOPrepared` | float |  |
| `QtySOShipped` | float |  |
| `QtySOShipping` | float |  |
| `SiteID` | int | **used** |
| `SubItemID` | int |  |
| `business_key` | str |  |

## INTran

| field | type | used |
|---|---|---|
| `AMBatNbr` | null |  |
| `AMDocType` | null |  |
| `AMLineNbr` | null |  |
| `AMOrderType` | null |  |
| `AMProdOrdID` | null |  |
| `ARDocType` | null |  |
| `ARLineNbr` | null |  |
| `ARRefNbr` | null |  |
| `AccrueCost` | bool |  |
| `AcctID` | int |  |
| `AppointmentLineNbr` | null |  |
| `AppointmentRefNbr` | null |  |
| `AssyType` | null |  |
| `AvgCost` | float |  |
| `BAccountID` | null |  |
| `BaseOrigFullQty` | null |  |
| `BaseQty` | float | **used** |
| `BranchID` | int |  |
| `COGSAcctID` | null |  |
| `COGSSubID` | null |  |
| `CostCenterID` | int |  |
| `CostedQty` | int |  |
| `CreatedByID` | str |  |
| `CreatedByScreenID` | str |  |
| `CreatedDateTime` | str |  |
| `DestBranchID` | null |  |
| `DocType` | str | **used** |
| `ExactCost` | bool |  |
| `ExpireDate` | null |  |
| `FinPeriodID` | str |  |
| `INTransitBaseQty` | null |  |
| `INTransitQty` | null |  |
| `InventoryID` | int | **used** |
| `InventorySource` | str |  |
| `InvtAcctID` | int |  |
| `InvtMult` | int | **used** |
| `InvtSubID` | int |  |
| `IsComponentItem` | bool |  |
| `IsCostUnmanaged` | null |  |
| `IsIntercompany` | bool |  |
| `IsStockItem` | bool |  |
| `IsUnassigned` | bool |  |
| `LastModifiedByID` | str |  |
| `LastModifiedByScreenID` | str |  |
| `LastModifiedDateTime` | str |  |
| `LineNbr` | int | **used** |
| `LocationID` | int |  |
| `LotSerialNbr` | str |  |
| `ManualCost` | bool |  |
| `MaxTransferBaseQty` | null |  |
| `NoteID` | str |  |
| `NoteText` | str |  |
| `OrigDocType` | null |  |
| `OrigFullQty` | null |  |
| `OrigIsLotSerial` | null |  |
| `OrigLineNbr` | null |  |
| `OrigModule` | str |  |
| `OrigNoteID` | null |  |
| `OrigPlanType` | null |  |
| `OrigQty` | float |  |
| `OrigRefNbr` | str |  |
| `OrigToLocationID` | null |  |
| `OrigTranAmt` | null |  |
| `OrigTranCost` | null |  |
| `OrigTranType` | null |  |
| `OrigUOM` | null |  |
| `OverrideUnitCost` | null |  |
| `PIID` | null |  |
| `PILineNbr` | null |  |
| `POLineType` | null |  |
| `POReceiptLineNbr` | int |  |
| `POReceiptNbr` | str |  |
| `POReceiptType` | str |  |
| `ProjectID` | int |  |
| `Qty` | float |  |
| `ReasonCode` | str |  |
| `ReceiptedBaseQty` | null |  |
| `ReceiptedQty` | null |  |
| `ReclassificationProhibited` | bool |  |
| `RefNbr` | str | **used** |
| `Released` | bool |  |
| `ReleasedDateTime` | str |  |
| `SOLineType` | null |  |
| `SOOrderLineNbr` | null |  |
| `SOOrderNbr` | null |  |
| `SOOrderType` | null |  |
| `SOShipmentLineNbr` | null |  |
| `SOShipmentNbr` | null |  |
| `SOShipmentType` | null |  |
| `SalesMult` | null |  |
| `ServiceContractPeriodID` | null |  |
| `ServiceContractRefNbr` | str |  |
| `ServiceOrderLineNbr` | null |  |
| `ServiceOrderRefNbr` | null |  |
| `SiteID` | int | **used** |
| `SortOrder` | int |  |
| `SrvOrdType` | null |  |
| `SubID` | int |  |
| `TaskID` | null |  |
| `ToCostCenterID` | int |  |
| `ToInventorySource` | str |  |
| `ToLocationID` | null |  |
| `ToProjectID` | null |  |
| `ToSiteID` | null |  |
| `ToTaskID` | null |  |
| `TranAmt` | float |  |
| `TranCost` | float |  |
| `TranDate` | str | **used** |
| `TranDesc` | str |  |
| `TranPeriodID` | str |  |
| `TranType` | str | **used** |
| `UOM` | str |  |
| `UnassignedQty` | float |  |
| `UnitCost` | float |  |
| `UnitPrice` | float |  |
| `UpdateShippedNotInvoiced` | bool |  |
| `business_key` | str |  |
| `tstamp` | str |  |

## POLine

| field | type | used |
|---|---|---|
| `AlternateID` | str |  |
| `BaseOpenQty` | float | **used** |
| `BaseOrderQty` | float |  |
| `BaseReceivedQty` | float |  |
| `CalcCuryOpenAmt` | float |  |
| `CalcOpenQty` | float |  |
| `Cancelled` | bool | **used** |
| `Completed` | bool | **used** |
| `CuryExtCost` | float |  |
| `CuryID` | str |  |
| `CuryInfoID` | int |  |
| `CuryLineAmt` | float |  |
| `CuryUnbilledAmt` | float |  |
| `CuryUnitCost` | float |  |
| `ExpenseAcctID` | null |  |
| `ExtCost` | float |  |
| `HasMultipleProjects` | bool |  |
| `InventoryID` | int | **used** |
| `LineAmount` | float |  |
| `LineNbr` | int | **used** |
| `LineType` | str | **used** |
| `OpenQty` | float |  |
| `OrderDate` | str | **used** |
| `OrderNbr` | str | **used** |
| `OrderQty` | float |  |
| `OrderType` | str | **used** |
| `ProjectID` | int |  |
| `PromisedDate` | str | **used** |
| `ReceivedQty` | float |  |
| `SiteID` | int | **used** |
| `TaskID` | null |  |
| `TaxCategoryID` | str |  |
| `TranDesc` | str |  |
| `UOM` | str |  |
| `UnbilledQty` | float |  |
| `UnitCost` | float |  |
| `VendorID` | int | **used** |
| `VendorRefNbr` | null |  |
| `business_key` | str |  |

## POReceiptLine

| field | type | used |
|---|---|---|
| `AccrueCost` | bool |  |
| `AllowComplete` | null |  |
| `AllowEditUnitCost` | bool |  |
| `AllowOpen` | null |  |
| `AllowResetCorrectionLine` | bool |  |
| `AlternateID` | str |  |
| `BaseMultReceiptQty` | float |  |
| `BaseOrigQty` | float |  |
| `BaseReceiptQty` | float |  |
| `BaseReturnedQty` | float |  |
| `BaseUnbilledQty` | float |  |
| `BillPPVAmt` | float |  |
| `BranchID` | int |  |
| `CanceledWithoutCorrection` | bool |  |
| `CostCenterID` | int |  |
| `CreatedByID` | str |  |
| `CreatedByScreenID` | str |  |
| `CreatedDateTime` | str |  |
| `CuryDiscAmt` | float |  |
| `CuryExtCost` | float |  |
| `CuryInfoID` | int |  |
| `CuryLineAmt` | null |  |
| `CuryTranCost` | float |  |
| `CuryTranUnitCost` | float |  |
| `CuryUnitCost` | float |  |
| `DiscAmt` | float |  |
| `DiscPct` | float |  |
| `DocumentDiscountRate` | float |  |
| `DropshipExpenseRecording` | null |  |
| `ExpenseAcctID` | int |  |
| `ExpenseSubID` | int |  |
| `ExpireDate` | str |  |
| `ExtCost` | float |  |
| `ExtVolume` | float |  |
| `ExtWeight` | float |  |
| `GroupDiscountRate` | float |  |
| `INReleased` | bool |  |
| `IntercompanyShipmentLineNbr` | null |  |
| `InventoryID` | int | **used** |
| `InvtMult` | int | **used** |
| `IsAdjusted` | bool |  |
| `IsAdjustedIN` | bool |  |
| `IsCorrection` | bool |  |
| `IsIntercompany` | bool |  |
| `IsKit` | bool |  |
| `IsLSEntryBlocked` | null |  |
| `IsSpecialOrder` | bool |  |
| `IsStockItem` | bool |  |
| `IsUnassigned` | bool |  |
| `LastModifiedByID` | str |  |
| `LastModifiedByScreenID` | str |  |
| `LastModifiedDateTime` | str |  |
| `LineNbr` | int | **used** |
| `LineType` | str |  |
| `LocationID` | int |  |
| `LotSerialNbr` | str |  |
| `LotSerialNbrRequiredForDropship` | bool |  |
| `ManualPrice` | bool |  |
| `MaxTransferBaseQty` | null |  |
| `NoteID` | str |  |
| `NoteText` | str |  |
| `OpenOrderQty` | null |  |
| `OrigDocType` | null |  |
| `OrigIsFixedInTransit` | null |  |
| `OrigIsLotSerial` | null |  |
| `OrigLineNbr` | null |  |
| `OrigNoteID` | null |  |
| `OrigOrderQty` | null |  |
| `OrigPlanType` | null |  |
| `OrigReceiptLineNbr` | int |  |
| `OrigReceiptLineType` | str |  |
| `OrigReceiptNbr` | str |  |
| `OrigReceiptType` | str |  |
| `OrigRefNbr` | null |  |
| `OrigToLocationID` | null |  |
| `OrigTranType` | null |  |
| `POAccrualAcctID` | int |  |
| `POAccrualLineNbr` | int |  |
| `POAccrualRefNoteID` | str |  |
| `POAccrualSubID` | int |  |
| `POAccrualType` | str |  |
| `POLineNbr` | int | **used** |
| `PONbr` | str | **used** |
| `POType` | str | **used** |
| `ProjectID` | int |  |
| `ReasonCode` | str |  |
| `ReceiptDate` | str | **used** |
| `ReceiptNbr` | str | **used** |
| `ReceiptQty` | float |  |
| `ReceiptType` | str | **used** |
| `ReceivedToDateQty` | float |  |
| `Released` | bool |  |
| `ReturnedQty` | null |  |
| `SOOrderLineNbr` | null |  |
| `SOOrderNbr` | null |  |
| `SOOrderType` | null |  |
| `SOShipmentNbr` | null |  |
| `SOShipmentType` | null |  |
| `SiteID` | int | **used** |
| `SortOrder` | int |  |
| `TaskID` | null |  |
| `TranCost` | float |  |
| `TranCostFinal` | float |  |
| `TranDesc` | str |  |
| `TranType` | str |  |
| `TranUnitCost` | float |  |
| `UOM` | str |  |
| `UnassignedQty` | float |  |
| `UnbilledQty` | float |  |
| `UnitCost` | float |  |
| `UnitVolume` | float |  |
| `UnitWeight` | float |  |
| `VendorID` | int | **used** |
| `business_key` | str |  |
| `tstamp` | str |  |

## BAccount

| field | type | used |
|---|---|---|
| `AcctCD` | str | **used** |
| `AcctName` | str | **used** |
| `AcctReferenceNbr` | str |  |
| `AllowOverrideCury` | bool |  |
| `AllowOverrideRate` | bool |  |
| `BAccountID` | int | **used** |
| `BaseCuryID` | str |  |
| `COrgBAccountID` | int |  |
| `CampaignSourceID` | str |  |
| `CasesCount` | null |  |
| `ClassID` | str |  |
| `ConsolidateToParent` | bool |  |
| `ConsolidatingBAccountID` | int |  |
| `Count` | null |  |
| `CreatedByID` | str |  |
| `CreatedByScreenID` | str |  |
| `CreatedDateTime` | str |  |
| `CuryID` | str |  |
| `CuryRateTypeID` | str |  |
| `DefAddressID` | int |  |
| `DefContactID` | int |  |
| `DefLocationID` | int |  |
| `DeletedDatabaseRecord` | bool |  |
| `EntityTypeID` | null |  |
| `HSEntityTypeID` | null |  |
| `IsBranch` | bool |  |
| `IsCustomerOrCombined` | bool |  |
| `LastActivity` | null |  |
| `LastModifiedByID` | str |  |
| `LastModifiedByScreenID` | str |  |
| `LastModifiedDateTime` | str |  |
| `LegalName` | str |  |
| `LocaleName` | null |  |
| `NoteID` | str |  |
| `NotePopupText` | str |  |
| `NoteText` | str |  |
| `OverrideSalesTerritory` | bool |  |
| `OwnerID` | int |  |
| `ParentBAccountID` | int |  |
| `PreviewHtml` | null |  |
| `PrimaryContactID` | int |  |
| `RegisteredEntityForReporting` | bool |  |
| `SalesTerritoryID` | str |  |
| `Secured` | null |  |
| `Status` | str | **used** |
| `TaxRegistrationID` | null |  |
| `Type` | str | **used** |
| `VOrgBAccountID` | int |  |
| `VStatus` | str |  |
| `ViewInCrm` | null |  |
| `WorkgroupID` | int |  |
| `business_key` | str |  |
| `tstamp` | str |  |

## SOLine

| field | type | used |
|---|---|---|
| `AMCTPAccepted` | bool |  |
| `AMConfigKeyID` | null |  |
| `AMConfigurationID` | null |  |
| `AMEstimateID` | null |  |
| `AMEstimateRevisionID` | null |  |
| `AMIsSupplemental` | bool |  |
| `AMOrderType` | null |  |
| `AMOrigParentLineNbr` | null |  |
| `AMOrigRequestDate` | null |  |
| `AMParentLineNbr` | int |  |
| `AMProdBaseQtyComplete` | float |  |
| `AMProdCreate` | bool |  |
| `AMProdOrdID` | null |  |
| `AMProdQtyComplete` | float |  |
| `AMProdStatusID` | null |  |
| `AMQtyReadOnly` | float |  |
| `AMSchdNoteID` | null |  |
| `AMSelected` | bool |  |
| `AMUOMReadOnly` | str |  |
| `AlternateID` | null |  |
| `AppointmentLineNbr` | null |  |
| `AppointmentRefNbr` | null |  |
| `AutoCreateIssueLine` | bool |  |
| `AutomaticDiscountsDisabled` | bool |  |
| `AvalaraCustomerUsageType` | str |  |
| `AvgCost` | null |  |
| `BaseBilledQty` | float |  |
| `BaseClosedQty` | float |  |
| `BaseOpenQty` | float | **used** |
| `BaseOrderQty` | float |  |
| `BaseQtyOnOrders` | float |  |
| `BaseShippedQty` | float |  |
| `BaseUnbilledQty` | float |  |
| `Behavior` | str |  |
| `BilledAmt` | float |  |
| `BilledQty` | float |  |
| `BlanketLineNbr` | null |  |
| `BlanketNbr` | null |  |
| `BlanketOpenQty` | float |  |
| `BlanketSplitLineNbr` | null |  |
| `BlanketType` | null |  |
| `BranchID` | int |  |
| `CalculateDiscountsOnImport` | null |  |
| `CancelDate` | str |  |
| `Cancelled` | bool | **used** |
| `ChildLineCntr` | int |  |
| `ClosedQty` | float |  |
| `Comment` | str |  |
| `Commissionable` | bool |  |
| `CommnAmt` | null |  |
| `CommnPct` | null |  |
| `CompleteQtyMax` | float |  |
| `CompleteQtyMin` | float |  |
| `Completed` | bool | **used** |
| `ComponentID` | null |  |
| `CostCenterID` | int |  |
| `CreateSubAssemblyOrders` | bool |  |
| `CreatedByID` | str |  |
| `CreatedByScreenID` | str |  |
| `CreatedDateTime` | str |  |
| `CuryBilledAmt` | float |  |
| `CuryCommnAmt` | null |  |
| `CuryDiscAmt` | float |  |
| `CuryDiscPrice` | float |  |
| `CuryExtCost` | float |  |
| `CuryExtPrice` | float |  |
| `CuryID` | null |  |
| `CuryInfoID` | int |  |
| `CuryLineAmt` | float |  |
| `CuryMarginAmt` | float |  |
| `CuryNetSales` | float |  |
| `CuryOpenAmt` | float |  |
| `CuryRate` | null |  |
| `CuryTaxableAmt` | float |  |
| `CuryUnbilledAmt` | float |  |
| `CuryUnitCost` | float |  |
| `CuryUnitPrice` | float |  |
| `CuryUnitPriceDR` | float |  |
| `CuryViewState` | null |  |
| `CustomerID` | int |  |
| `CustomerLocationID` | int |  |
| `CustomerOrderNbr` | null |  |
| `DRTermEndDate` | null |  |
| `DRTermStartDate` | null |  |
| `DefScheduleID` | null |  |
| `DefaultOperation` | str |  |
| `DisableAutomaticTaxCalculation` | bool |  |
| `DiscAmt` | float |  |
| `DiscPct` | float |  |
| `DiscPctDR` | float |  |
| `DiscPrice` | float |  |
| `DiscountID` | null |  |
| `DiscountSequenceID` | null |  |
| `DocumentDiscountRate` | float |  |
| `EquipmentAction` | str |  |
| `EquipmentComponentLineNbr` | null |  |
| `EquipmentItemClass` | str |  |
| `ExpireDate` | null |  |
| `ExtCost` | float |  |
| `ExtPrice` | float |  |
| `ExtVolume` | float |  |
| `ExtWeight` | float |  |
| `FOBPoint` | null |  |
| `FreezeManualDisc` | null |  |
| `GroupDiscountRate` | float |  |
| `HistoryLineID` | null |  |
| `IntercompanyPOLineNbr` | null |  |
| `InventoryID` | int | **used** |
| `InventorySource` | str |  |
| `InvoiceDate` | null |  |
| `InvoiceLineNbr` | null |  |
| `InvoiceNbr` | null |  |
| `InvoiceType` | null |  |
| `InvoiceUOM` | null |  |
| `InvtMult` | int |  |
| `IsBeingCopied` | null |  |
| `IsConfigurable` | bool |  |
| `IsCut` | bool |  |
| `IsFree` | bool |  |
| `IsKit` | bool |  |
| `IsLegacyDropShip` | bool |  |
| `IsOrchestratedLine` | bool |  |
| `IsPOLinkAllowed` | bool |  |
| `IsPromotionalPrice` | bool |  |
| `IsStockItem` | bool |  |
| `ItemHasResidual` | bool |  |
| `ItemRequiresTerms` | null |  |
| `LastModifiedByID` | str |  |
| `LastModifiedByScreenID` | str |  |
| `LastModifiedDateTime` | str |  |
| `LineAmt` | float |  |
| `LineNbr` | int | **used** |
| `LineQtyAvail` | null |  |
| `LineQtyHardAvail` | null |  |
| `LineSign` | int |  |
| `LineType` | str | **used** |
| `LocationID` | int |  |
| `LotSerialNbr` | str |  |
| `ManualDisc` | bool |  |
| `ManualPrice` | bool |  |
| `MarginAmt` | float |  |
| `MarginPct` | float |  |
| `NetSales` | float |  |
| `NewEquipmentLineNbr` | null |  |
| `NoteID` | str |  |
| `NoteText` | str |  |
| `OpenAmt` | float |  |
| `OpenChildLineCntr` | int |  |
| `OpenLine` | bool |  |
| `OpenQty` | float |  |
| `Operation` | str | **used** |
| `OrchestrationOriginalLineNbr` | null |  |
| `OrchestrationOriginalSiteID` | null |  |
| `OrchestrationPlanID` | null |  |
| `OrderDate` | str |  |
| `OrderNbr` | str | **used** |
| `OrderQty` | float |  |
| `OrderType` | str | **used** |
| `OrigIsSpecialOrder` | null |  |
| `OrigLineNbr` | null |  |
| `OrigOrderNbr` | null |  |
| `OrigOrderType` | null |  |
| `OrigPlanType` | null |  |
| `OrigShipmentType` | null |  |
| `POCreate` | bool |  |
| `POCreateDate` | str |  |
| `POCreated` | bool |  |
| `POLineNbr` | null |  |
| `POLinkActive` | null |  |
| `POOrderNbr` | null |  |
| `POOrderStatus` | null |  |
| `POOrderType` | null |  |
| `POSiteID` | int |  |
| `POSource` | null |  |
| `PlanType` | str |  |
| `PriceType` | str |  |
| `ProjectID` | int |  |
| `QtyOnOrders` | float |  |
| `ReasonCode` | null |  |
| `RelatedDocument` | null |  |
| `RelatedItems` | null |  |
| `RelatedItemsRelation` | null |  |
| `RelatedItemsRequired` | null |  |
| `RequestDate` | str | **used** |
| `RequireAllocation` | bool |  |
| `RequireLocation` | bool |  |
| `RequireReasonCode` | bool |  |
| `RequireShipping` | bool |  |
| `SDPosted` | bool |  |
| `SDSelected` | bool |  |
| `SMEquipmentID` | null |  |
| `SalesAcctID` | int |  |
| `SalesPersonID` | int |  |
| `SalesSubID` | int |  |
| `SchedOrderDate` | str |  |
| `SchedShipDate` | str |  |
| `ServiceContractPeriodID` | null |  |
| `ServiceContractRefNbr` | null |  |
| `ServiceOrderLineNbr` | null |  |
| `ServiceOrderRefNbr` | null |  |
| `ShipComplete` | str |  |
| `ShipDate` | str | **used** |
| `ShipTermsID` | null |  |
| `ShipVia` | null |  |
| `ShipZoneID` | null |  |
| `ShippedQty` | float |  |
| `SiteID` | int | **used** |
| `SkipDisc` | null |  |
| `SkipLineDiscounts` | bool |  |
| `SortOrder` | int |  |
| `SrvOrdType` | null |  |
| `SubstitutionRequired` | bool |  |
| `SuggestRelatedItems` | null |  |
| `TaskID` | null |  |
| `TaxCategoryID` | str |  |
| `TaxZoneID` | str |  |
| `TaxableAmt` | float |  |
| `TranDesc` | str |  |
| `TranType` | str |  |
| `UOM` | str |  |
| `UnassignedQty` | float |  |
| `UnbilledAmt` | float |  |
| `UnbilledQty` | float |  |
| `UnitCost` | float |  |
| `UnitPrice` | float |  |
| `UnitVolume` | float |  |
| `UnitWeigth` | float |  |
| `UnshippedQty` | float |  |
| `VendorID` | null |  |
| `VerifyOrderQty` | float |  |
| `business_key` | str |  |
| `tstamp` | str |  |

## POVendorInventory

| field | type | used |
|---|---|---|
| `Active` | bool | **used** |
| `AddLeadTimeDays` | int | **used** |
| `AllLocations` | bool |  |
| `CreatedByID` | str |  |
| `CreatedByScreenID` | str |  |
| `CreatedDateTime` | str |  |
| `CuryID` | str |  |
| `ERQ` | float |  |
| `InventoryID` | int | **used** |
| `IsDefault` | bool | **used** |
| `LastModifiedByID` | str |  |
| `LastModifiedByScreenID` | str |  |
| `LastModifiedDateTime` | str |  |
| `LastPrice` | float |  |
| `LotSize` | float | **used** |
| `MaxOrdQty` | float | **used** |
| `MinOrdFreq` | int |  |
| `MinOrdQty` | float | **used** |
| `NoteID` | str |  |
| `NoteText` | str |  |
| `OverrideSettings` | bool |  |
| `PrepaymentPct` | null |  |
| `PurchaseUnit` | str |  |
| `RecordID` | int | **used** |
| `VLeadTime` | int | **used** |
| `VendorID` | int | **used** |
| `VendorInventoryID` | str |  |
| `VendorLocationID` | int |  |
| `business_key` | str |  |
| `tstamp` | str |  |
